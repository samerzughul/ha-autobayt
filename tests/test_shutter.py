"""Shutter control through the up and down channels."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.autobayt import const
from custom_components.autobayt.const import CONF_SWAP_DIRECTION, CONF_TRAVEL_TIME
from homeassistant.components.cover import ATTR_CURRENT_POSITION, ATTR_POSITION
from homeassistant.const import ATTR_ASSUMED_STATE, ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.exceptions import HomeAssistantError

from .conftest import eid, state_of

SHUTTER = "autobayt_sh-1_shutter"
OPEN = {"device_id": "sh-1", "btnIds": [1, 0], "btnStates": [False, True]}
CLOSE = {"device_id": "sh-1", "btnIds": [0, 1], "btnStates": [False, True]}
STOP = {"device_id": "sh-1", "btnIds": [0, 1], "btnStates": [False, False]}


@pytest.fixture(autouse=True)
def short_pause(monkeypatch):
    """No reversal pause in tests: the frozen clock would never let it end."""
    monkeypatch.setattr(const, "SHUTTER_REVERSE_PAUSE", 0)


async def _setup(hass, fake, config_entry, **options):
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options=options)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def _call(hass, service, **data):
    await hass.services.async_call(
        "cover", service, {ATTR_ENTITY_ID: eid(hass, SHUTTER), **data}, blocking=True
    )
    await hass.async_block_till_done()


async def _tick(hass, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _never_both_on(fake) -> None:
    for trigger in fake.triggers:
        on = [b for b, s in zip(trigger["btnIds"], trigger["btnStates"], strict=True) if s]
        assert len(on) <= 1, trigger
        # A move always switches the other motor channel off in the same request.
        if on:
            assert set(trigger["btnIds"]) == {0, 1}


async def test_open_close_stop(hass, fake, config_entry):
    await _setup(hass, fake, config_entry)
    await _call(hass, "open_cover")
    assert fake.triggers == [OPEN]
    assert state_of(hass, SHUTTER).state == "opening"
    await _call(hass, "stop_cover")
    assert fake.triggers[-1] == STOP
    await _call(hass, "close_cover")
    assert fake.triggers[-1] == CLOSE
    assert state_of(hass, SHUTTER).state == "closing"
    _never_both_on(fake)


async def test_reversal_stops_first(hass, fake, config_entry):
    await _setup(hass, fake, config_entry)
    await _call(hass, "open_cover")
    await _call(hass, "close_cover")
    assert fake.triggers == [OPEN, STOP, CLOSE]
    await _call(hass, "open_cover")
    assert fake.triggers == [OPEN, STOP, CLOSE, STOP, OPEN]
    _never_both_on(fake)


async def test_same_direction_does_not_stop(hass, fake, config_entry):
    await _setup(hass, fake, config_entry)
    await _call(hass, "open_cover")
    await _call(hass, "open_cover")
    assert fake.triggers == [OPEN, OPEN]


async def test_motion_seen_from_reported_channels(hass, fake, config_entry, freezer):
    """A move started in the Autobayt app or on the wall shows as moving."""
    await _setup(hass, fake, config_entry)
    fake.devices[3]["buttons"][1]["switch_state"] = True
    await _tick(hass, freezer, 10)
    assert state_of(hass, SHUTTER).state == "closing"
    fake.devices[3]["buttons"][1]["switch_state"] = False
    await _tick(hass, freezer, 10)
    assert state_of(hass, SHUTTER).state == "open"


async def test_swap_direction(hass, fake, config_entry):
    await _setup(hass, fake, config_entry, **{CONF_SWAP_DIRECTION: True})
    await _call(hass, "open_cover")
    assert fake.triggers == [{"device_id": "sh-1", "btnIds": [0, 1], "btnStates": [False, True]}]
    await _call(hass, "stop_cover")
    assert fake.triggers[-1] == {"device_id": "sh-1", "btnIds": [1, 0], "btnStates": [False, False]}


async def test_full_open_stops_after_travel_time(hass, fake, config_entry, freezer):
    await _setup(hass, fake, config_entry, **{CONF_TRAVEL_TIME: 20})
    fake.apply_triggers = False
    await _call(hass, "open_cover")
    assert fake.triggers == [OPEN]
    # The test clock fires timers up to half a second early, so allow a second.
    await _tick(hass, freezer, 20 * const.SHUTTER_END_MARGIN - 1)
    assert fake.triggers == [OPEN]
    await _tick(hass, freezer, 2)
    assert fake.triggers == [OPEN, STOP]


async def test_set_position_times_the_motor(hass, fake, config_entry, freezer):
    await _setup(hass, fake, config_entry, **{CONF_TRAVEL_TIME: 20})
    fake.apply_triggers = False
    state = state_of(hass, SHUTTER)
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == 15
    assert state.attributes[ATTR_CURRENT_POSITION] == 40
    await _call(hass, "set_cover_position", **{ATTR_POSITION: 60})
    assert fake.triggers == [OPEN]  # 20 % of 20 s is 4 s up
    await _tick(hass, freezer, 2)
    assert state_of(hass, SHUTTER).state == "opening"
    await _tick(hass, freezer, 1)
    assert fake.triggers == [OPEN]
    await _tick(hass, freezer, 2)
    assert fake.triggers == [OPEN, STOP]
    # Down again: 30 % of 20 s is 6 s.
    fake.devices[3]["perc"] = 60
    await _tick(hass, freezer, 10)
    await _call(hass, "set_cover_position", **{ATTR_POSITION: 30})
    assert fake.triggers[-1] == CLOSE
    await _tick(hass, freezer, 6.1)
    assert fake.triggers[-1] == STOP
    _never_both_on(fake)


async def test_stop_mid_timed_move_remembers_position(hass, fake, config_entry, freezer):
    del fake.devices[3]["perc"]
    await _setup(hass, fake, config_entry, **{CONF_TRAVEL_TIME: 20})
    fake.apply_triggers = False
    # Unknown position: a partial move is refused until one full run.
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "set_cover_position", **{ATTR_POSITION: 50})
    assert err.value.translation_key == "position_unknown"
    await _call(hass, "close_cover")
    await _tick(hass, freezer, 23)
    assert fake.triggers == [CLOSE, STOP]
    assert state_of(hass, SHUTTER).attributes[ATTR_CURRENT_POSITION] == 0
    await _call(hass, "open_cover")
    await _tick(hass, freezer, 5)
    await _call(hass, "stop_cover")
    assert fake.triggers[-1] == STOP
    assert state_of(hass, SHUTTER).attributes[ATTR_CURRENT_POSITION] == 25
    assert state_of(hass, SHUTTER).state == "open"


async def test_set_position_while_moving_stops_first(hass, fake, config_entry, freezer):
    await _setup(hass, fake, config_entry, **{CONF_TRAVEL_TIME: 20})
    fake.apply_triggers = False
    await _call(hass, "close_cover")
    await _tick(hass, freezer, 4)  # now about 20 %
    await _call(hass, "set_cover_position", **{ATTR_POSITION: 50})
    assert fake.triggers == [CLOSE, STOP, OPEN]
    _never_both_on(fake)


async def test_assumed_state_without_position_or_travel_time(hass, fake, config_entry):
    del fake.devices[3]["perc"]
    await _setup(hass, fake, config_entry)
    state = state_of(hass, SHUTTER)
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == 11
    assert state.attributes[ATTR_ASSUMED_STATE] is True
    assert state.state == "unknown"


async def test_set_position_needs_travel_time(hass, fake, config_entry):
    from custom_components.autobayt.cover import AutobaytShutter

    entry = await _setup(hass, fake, config_entry)
    cover = AutobaytShutter(entry.runtime_data.devices, "sh-1")
    cover.hass = hass
    with pytest.raises(HomeAssistantError) as err:
        await cover.async_set_cover_position(position=50)
    assert err.value.translation_key == "no_travel_time"
    assert fake.triggers == []


async def test_unload_mid_move_stops_the_motor(hass, fake, config_entry):
    entry = await _setup(hass, fake, config_entry, **{CONF_TRAVEL_TIME: 20})
    await _call(hass, "open_cover")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fake.triggers == [OPEN, STOP]


async def test_failed_command_is_reported(hass, fake, config_entry, aioclient_mock):
    await _setup(hass, fake, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.post("https://api.autobayt.com/v1/device/trigger", status=400)
    fake.register(aioclient_mock)  # get-things still works; trigger matches the 400 first
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "open_cover")
    assert err.value.translation_key == "command_failed"
