"""Energy sensors, diagnostics and the API client."""

from __future__ import annotations

import json
import re

import aiohttp
import pytest

from custom_components.autobayt.api import (
    AutobaytAuthError,
    AutobaytClient,
    AutobaytConnectionError,
    AutobaytRateLimitError,
    AutobaytResponseError,
    ShutterControlUnavailable,
)
from custom_components.autobayt.const import ENERGY_LIVE_INTERVAL
from custom_components.autobayt.diagnostics import async_get_config_entry_diagnostics
from custom_components.autobayt.models import is_shutter, parse_device, public_fields
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import LOC_ID, TOKEN, USER_ID, state_of


async def test_energy_sensors(hass, setup_integration):
    assert float(state_of(hass, "autobayt_pm-1_power").state) == 1450.0
    assert float(state_of(hass, "autobayt_pm-1_energy_today").state) == 3.1
    assert float(state_of(hass, f"autobayt_{LOC_ID}_power").state) == 1500.5
    assert float(state_of(hass, f"autobayt_{LOC_ID}_energy_today").state) == 4.2
    # Non-PM devices get no energy sensors.
    assert state_of(hass, "autobayt_sw-1_power") is None


async def test_null_energy_is_unavailable_not_zero(hass, fake, config_entry):
    fake.energy_live = {
        "power_w": None,
        "by_device": [{"device_id": "pm-1", "power_w": None, "status": "stale"}],
    }
    fake.energy_today = {"consumption_kwh": 1.0, "status": "no_data", "by_device": []}
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert state_of(hass, "autobayt_pm-1_power").state == STATE_UNAVAILABLE
    assert state_of(hass, f"autobayt_{LOC_ID}_power").state == STATE_UNAVAILABLE
    assert state_of(hass, f"autobayt_{LOC_ID}_energy_today").state == STATE_UNAVAILABLE
    assert state_of(hass, "autobayt_pm-1_energy_today").state == STATE_UNAVAILABLE


async def test_energy_failure_does_not_block_switches(hass, fake, config_entry, aioclient_mock):
    aioclient_mock.clear_requests()
    aioclient_mock.get(re.compile(r".*/v1/user/energy.*"), status=500)
    fake.register(aioclient_mock)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert state_of(hass, "autobayt_pm-1_0").state == "off"
    assert state_of(hass, "autobayt_pm-1_power").state == STATE_UNAVAILABLE


async def test_diagnostics_redacted(hass, setup_integration):
    result = await async_get_config_entry_diagnostics(hass, setup_integration)
    text = json.dumps(result)
    for secret in (TOKEN, USER_ID, LOC_ID, "sw-1", "pm-1"):
        assert secret not in text
    assert len(result["devices"]) == 4
    shutter = next(d for d in result["devices"] if d["is_shutter"])
    assert shutter["raw"]["loc_id"] == "**REDACTED**"
    assert shutter["raw"]["buttons"][2] == {"button_id": 2, "name": "Dim", "switch_state": True}


async def test_client_filters_location(hass, fake):
    client = AutobaytClient(async_get_clientsession(hass), "https://api.autobayt.com", TOKEN)
    devices = await client.get_things(USER_ID, LOC_ID)
    assert {d["device_id"] for d in devices} == {"sw-1", "pm-1", "off-1", "sh-1"}


async def test_client_rejects_mismatched_arrays(hass):
    client = AutobaytClient(async_get_clientsession(hass), "https://api.autobayt.com", TOKEN)
    with pytest.raises(ValueError):
        await client.set_buttons("sw-1", [0, 1], [True])


async def test_shutter_control_not_available(hass):
    client = AutobaytClient(async_get_clientsession(hass), "https://api.autobayt.com", TOKEN)
    with pytest.raises(ShutterControlUnavailable):
        await client.shutter_command("sh-1", "open")


@pytest.mark.parametrize(
    ("raw", "extra", "expected"),
    [
        ({"model_name": "SW200LV1"}, [], False),
        ({"model_name": "SW100PMV1", "perc": 30}, [], False),
        ({"model_name": "Shutter controller"}, [], True),
        ({"model_name": "CT100V1"}, [], True),
        ({"model_name": "XY9"}, ["xy9"], True),
    ],
)
def test_shutter_detection(raw, extra, expected):
    assert is_shutter(raw, extra) is expected


async def test_diagnostics_hide_camel_case_and_embedded_ids(hass, fake, config_entry):
    fake.devices[0]["deviceId"] = "sw-1"
    fake.devices[0]["macAddress"] = "aa:bb:cc:dd:ee:ff"
    fake.devices[0]["ipAddress"] = "10.0.0.5"
    fake.devices[0]["topic"] = "things/sw-1/state"
    fake.devices[0]["note"] = f"belongs to {USER_ID} at {LOC_ID}"
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    text = json.dumps(await async_get_config_entry_diagnostics(hass, config_entry))
    for secret in ("sw-1", "aa:bb:cc", "10.0.0.5", USER_ID, LOC_ID, TOKEN):
        assert secret not in text
    assert "SW200LV1" in text  # model names stay, they help with support


def test_public_fields_keeps_harmless_values():
    raw = {"model_name": "SW1", "buttons": [{"button_id": 0, "name": "Lamp"}], "perc": 3}
    assert public_fields(raw, ["xyz-device"]) == raw


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (403, None, AutobaytAuthError),
        (429, None, AutobaytRateLimitError),
        (503, None, AutobaytConnectionError),
        (404, {"error": "x"}, AutobaytResponseError),
        (200, {"not": "a list"}, AutobaytResponseError),
    ],
)
async def test_client_maps_errors(hass, aioclient_mock, status, body, error):
    aioclient_mock.get(re.compile(r".*/v1/user/get-things.*"), status=status, json=body)
    client = AutobaytClient(async_get_clientsession(hass), "https://api.autobayt.com", TOKEN)
    with pytest.raises(error):
        await client.get_things(USER_ID, LOC_ID)


async def test_client_timeout_and_network_errors(hass, aioclient_mock):
    client = AutobaytClient(async_get_clientsession(hass), "https://api.autobayt.com", TOKEN)
    aioclient_mock.get(re.compile(r".*/v1/device\?.*"), exc=TimeoutError())
    with pytest.raises(AutobaytConnectionError):
        await client.get_device("sw-1")
    aioclient_mock.clear_requests()
    aioclient_mock.get(re.compile(r".*/v1/device\?.*"), exc=aiohttp.ClientConnectionError())
    with pytest.raises(AutobaytConnectionError):
        await client.get_device("sw-1")


async def test_client_rate_limit_hint(hass, aioclient_mock):
    aioclient_mock.get(
        re.compile(r".*/v1/user/energy/live.*"), status=429, headers={"Retry-After": "12"}
    )
    client = AutobaytClient(async_get_clientsession(hass), "https://api.autobayt.com", TOKEN)
    with pytest.raises(AutobaytRateLimitError) as err:
        await client.get_energy_live(LOC_ID)
    assert err.value.retry_after == 12


async def test_energy_backs_off_and_recovers(hass, setup_integration, fake, aioclient_mock):
    energy = setup_integration.runtime_data.energy
    aioclient_mock.clear_requests()
    aioclient_mock.get(re.compile(r".*/v1/user/energy.*"), status=503)
    fake.register(aioclient_mock)
    await energy.async_refresh()
    assert not energy.last_update_success
    assert energy.update_interval > ENERGY_LIVE_INTERVAL
    aioclient_mock.clear_requests()
    fake.register(aioclient_mock)
    await energy.async_refresh()
    assert energy.last_update_success
    assert energy.update_interval == ENERGY_LIVE_INTERVAL


def test_parse_device_tolerates_odd_fields():
    device = parse_device(
        {
            "device_id": 7,
            "model_name": "SH300V1",
            "type": None,
            "buttons": [
                "junk",
                {"name": "no id"},
                {"button_id": "x"},
                {"button_id": "0", "switch_state": "on"},
                {"button_id": 1, "switch_state": 0},
                {"button_id": 2, "switch_state": "maybe", "perc": "250"},
            ],
        }
    )
    assert device.device_id == "7"
    assert device.name == "SH300V1"
    assert device.is_shutter
    assert device.position == 100  # read from the channel, clamped
    assert device.buttons[0].is_on is True
    assert device.buttons[1].is_on is False
    assert device.buttons[2].is_on is None
    assert device.relay_channels() == [2]


@pytest.mark.parametrize("perc", [None, True, "n/a", [1]])
def test_shutter_without_usable_position(perc):
    device = parse_device({"device_id": "s", "model_name": "SH300V1", "perc": perc})
    assert device.position is None


def test_shutter_detected_by_type_field():
    assert is_shutter({"model_name": "X1", "device_type": "Roller blind"})
