"""Switches, shutters, polling, commands and error handling."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.autobayt.const import DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import TOKEN, eid, state_of


async def _tick(hass, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_switches_created_per_channel(hass, setup_integration):
    ent_reg = er.async_get(hass)
    ceiling = state_of(hass, "autobayt_sw-1_0")
    wall = state_of(hass, "autobayt_sw-1_1")
    assert ceiling.state == STATE_ON
    assert wall.state == STATE_OFF
    assert ent_reg.async_get(ceiling.entity_id).unique_id == "autobayt_sw-1_0"
    assert ent_reg.async_get(wall.entity_id).unique_id == "autobayt_sw-1_1"
    # Single-channel device whose channel shares the device name takes the device name.
    assert state_of(hass, "autobayt_pm-1_0").state == STATE_OFF


async def test_other_locations_filtered(hass, setup_integration):
    ent_reg = er.async_get(hass)
    ids = {
        e.unique_id for e in er.async_entries_for_config_entry(ent_reg, setup_integration.entry_id)
    }
    assert not any("elsewhere-1" in uid for uid in ids)


async def test_device_registry(hass, setup_integration):
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device_by_identifier((DOMAIN, "sw-1"), setup_integration.entry_id)
    assert device.manufacturer == "Autobayt"
    assert device.model == "SW200LV1"
    assert device.sw_version == "1.10.1"
    assert device.name == "Hallway switch"
    assert device.area_id is not None


async def test_disconnected_device_unavailable(hass, setup_integration):
    assert state_of(hass, "autobayt_off-1_0").state == STATE_UNAVAILABLE


async def test_shutter_is_read_only_cover(hass, setup_integration):
    state = state_of(hass, "autobayt_sh-1_shutter")
    assert state is not None
    assert state.attributes["current_position"] == 40
    assert state.state == "open"
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == 0
    # Shutter channels are never exposed as switches.
    assert state_of(hass, "autobayt_sh-1_0") is None
    assert state_of(hass, "autobayt_sh-1_1") is None
    # The extra light channel on the shutter plate is an ordinary switch.
    light = state_of(hass, "autobayt_sh-1_2")
    assert eid(hass, "autobayt_sh-1_2").startswith("light.")
    assert light.state == STATE_ON
    assert light.attributes["friendly_name"].endswith("Dim light")


async def test_turn_on_sends_trigger_with_token(hass, setup_integration, fake, aioclient_mock):
    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: eid(hass, "autobayt_sw-1_1")}, blocking=True
    )
    assert fake.triggers == [{"device_id": "sw-1", "btnIds": [1], "btnStates": [True]}]
    assert state_of(hass, "autobayt_sw-1_1").state == STATE_ON
    headers = [call[3] for call in aioclient_mock.mock_calls]
    assert all(h and h.get("Authorization") == f"Bearer {TOKEN}" for h in headers)


async def test_command_shown_until_confirmed_then_reverts(hass, setup_integration, fake, freezer):
    fake.apply_triggers = False  # the relay never reports the new state
    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: eid(hass, "autobayt_sw-1_1")}, blocking=True
    )
    assert state_of(hass, "autobayt_sw-1_1").state == STATE_ON
    calls = len(fake.triggers)
    await _tick(hass, freezer, 2)
    await _tick(hass, freezer, 3)
    assert state_of(hass, "autobayt_sw-1_1").state == STATE_ON
    await _tick(hass, freezer, 16)
    assert state_of(hass, "autobayt_sw-1_1").state == STATE_OFF
    assert len(fake.triggers) == calls


async def test_followup_refresh_after_command(
    hass, setup_integration, fake, freezer, aioclient_mock
):
    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: eid(hass, "autobayt_sw-1_0")}, blocking=True
    )
    before = sum(1 for c in aioclient_mock.mock_calls if "get-things" in str(c[1]))
    await _tick(hass, freezer, 2)
    after = sum(1 for c in aioclient_mock.mock_calls if "get-things" in str(c[1]))
    assert after == before + 1
    assert state_of(hass, "autobayt_sw-1_0").state == STATE_OFF


async def test_state_follows_polling(hass, setup_integration, fake, freezer):
    fake.devices[0]["buttons"][1]["switch_state"] = True
    await _tick(hass, freezer, 10)
    assert state_of(hass, "autobayt_sw-1_1").state == STATE_ON


async def test_backoff_on_server_errors(hass, setup_integration, fake, freezer):
    coordinator = setup_integration.runtime_data.devices
    fake.get_things_status = 503
    await _tick(hass, freezer, 10)
    assert state_of(hass, "autobayt_sw-1_0").state == STATE_UNAVAILABLE
    assert coordinator.update_interval == timedelta(seconds=20)
    fake.get_things_status = 429
    await _tick(hass, freezer, 20)
    assert coordinator.update_interval >= timedelta(seconds=40)
    fake.get_things_status = 200
    await _tick(hass, freezer, coordinator.update_interval.total_seconds())
    assert coordinator.update_interval == timedelta(seconds=10)
    assert state_of(hass, "autobayt_sw-1_0").state == STATE_ON


async def test_401_starts_reauth(hass, setup_integration, fake, freezer):
    fake.get_things_status = 401
    await _tick(hass, freezer, 10)
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == SOURCE_REAUTH for f in flows)


async def test_401_at_startup_starts_reauth(hass, fake, config_entry):
    fake.get_things_status = 401
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == SOURCE_REAUTH for f in flows)


async def test_command_failure_raises(hass, setup_integration, aioclient_mock, fake):
    aioclient_mock.clear_requests()
    aioclient_mock.post("https://api.autobayt.com/v1/device/trigger", status=400)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "light",
            "turn_on",
            {ATTR_ENTITY_ID: eid(hass, "autobayt_sw-1_1")},
            blocking=True,
        )


async def test_new_device_added_on_poll(hass, setup_integration, fake, freezer):
    fake.devices.append(
        {
            "device_id": "sw-new",
            "name": "Porch",
            "model_name": "SW100HV1",
            "loc_id": fake.devices[0]["loc_id"],
            "connection_status": True,
            "buttons": [{"button_id": 0, "name": "Porch", "switch_state": True}],
        }
    )
    await _tick(hass, freezer, 10)
    assert state_of(hass, "autobayt_sw-new_0").state == STATE_ON


async def test_unload(hass, setup_integration):
    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    assert setup_integration.state is ConfigEntryState.NOT_LOADED


async def test_lights_and_switches_split_by_name(hass, setup_integration):
    assert eid(hass, "autobayt_sw-1_0").startswith("light.")  # "Ceiling light"
    assert eid(hass, "autobayt_sw-1_1").startswith("light.")  # "Wall light"
    assert eid(hass, "autobayt_pm-1_0").startswith("switch.")  # "Heater"
    assert eid(hass, "autobayt_off-1_0").startswith("switch.")  # "Garage"


async def test_channel_moving_platform_leaves_no_stale_entity(hass, fake, config_entry):
    ent_reg = er.async_get(hass)
    config_entry.add_to_hass(hass)
    old = ent_reg.async_get_or_create(
        "switch", DOMAIN, "autobayt_sw-1_0", config_entry=config_entry
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert ent_reg.async_get(old.entity_id) is None
    assert eid(hass, "autobayt_sw-1_0").startswith("light.")
