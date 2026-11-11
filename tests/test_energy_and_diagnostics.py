"""Energy sensors, diagnostics and the API client."""

from __future__ import annotations

import json
import re

import pytest

from custom_components.autobayt.api import AutobaytClient, ShutterControlUnavailable
from custom_components.autobayt.diagnostics import async_get_config_entry_diagnostics
from custom_components.autobayt.models import is_shutter
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
