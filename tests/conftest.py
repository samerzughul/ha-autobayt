"""Fixtures: a fake Autobayt backend behind Home Assistant's aiohttp mocker."""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.autobayt.const import (
    CONF_HOST,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    CONF_TOKEN,
    CONF_USER_ID,
    DEFAULT_HOST,
    DOMAIN,
)
from homeassistant.helpers import entity_registry as er

USER_ID = "aaaaaaaaaaaaaaaaaaaaaaaa"
LOC_ID = "bbbbbbbbbbbbbbbbbbbbbbbb"
OTHER_LOC_ID = "cccccccccccccccccccccccc"
TOKEN = "test-token"
EMAIL = "someone@example.com"
PASSWORD = "secret"

DEVICES: list[dict[str, Any]] = [
    {
        "device_id": "sw-1",
        "name": "Hallway switch",
        "model_name": "SW200LV1",
        "firmware_version": "1.10.1",
        "loc_id": LOC_ID,
        "room_name": "Hallway",
        "connection_status": True,
        "buttons": [
            {"button_id": 0, "name": "Ceiling light", "switch_state": True},
            {"button_id": 1, "name": "Wall light", "switch_state": False},
        ],
    },
    {
        "device_id": "pm-1",
        "name": "Heater",
        "model_name": "SW100PMV1",
        "firmware_version": "2.0.0",
        "loc_id": LOC_ID,
        "room_name": "Kitchen",
        "connection_status": True,
        "buttons": [{"button_id": 0, "name": "Heater", "switch_state": False}],
    },
    {
        "device_id": "off-1",
        "name": "Garage switch",
        "model_name": "SW100HV1",
        "loc_id": LOC_ID,
        "room_name": "Garage",
        "connection_status": False,
        "buttons": [{"button_id": 0, "name": "Garage", "switch_state": False}],
    },
    {
        "device_id": "sh-1",
        "name": "Living room shutter",
        "model_name": "SHUTTER-TEST",
        "loc_id": LOC_ID,
        "room_name": "Living room",
        "connection_status": True,
        "perc": 40,
        "buttons": [
            {"button_id": 0, "name": "Up", "switch_state": False},
            {"button_id": 1, "name": "Down", "switch_state": False},
        ],
    },
    {
        "device_id": "elsewhere-1",
        "name": "Other home switch",
        "model_name": "SW100HV1",
        "loc_id": OTHER_LOC_ID,
        "connection_status": True,
        "buttons": [{"button_id": 0, "name": "Lamp", "switch_state": True}],
    },
]


class FakeAutobayt:
    """In-memory Autobayt backend."""

    def __init__(self) -> None:
        self.devices = copy.deepcopy(DEVICES)
        self.locations = [{"_id": LOC_ID, "name": "Home", "user_id": USER_ID}]
        self.get_things_status = 200
        self.login_error_code: int | None = None
        self.apply_triggers = True
        self.triggers: list[dict[str, Any]] = []
        self.energy_live: dict[str, Any] = {
            "power_w": 1500.5,
            "status": "live",
            "by_device": [
                {"device_id": "pm-1", "power_w": 1450.0, "status": "live"},
            ],
        }
        self.energy_today: dict[str, Any] = {
            "consumption_kwh": 4.2,
            "by_device": [{"device_id": "pm-1", "consumption_kwh": 3.1}],
        }

    def _resp(self, method, url, status=200, json=None, headers=None):
        return AiohttpClientMockResponse(method, url, status=status, json=json, headers=headers)

    async def h_login(self, method, url, data):
        if self.login_error_code is not None:
            return self._resp(
                method, url, 400, {"error_code": self.login_error_code, "error_msg": "x"}
            )
        assert data == {"email": EMAIL, "password": PASSWORD, "source": "email"}
        return self._resp(
            method, url, json={"token": TOKEN, "user_info": {"_id": USER_ID, "email": EMAIL}}
        )

    async def h_locations(self, method, url, data):
        return self._resp(method, url, json=self.locations)

    async def h_get_things(self, method, url, data):
        status = self.get_things_status
        if status != 200:
            headers = {"Retry-After": "30"} if status == 429 else None
            return self._resp(method, url, status, {"error": "x"}, headers)
        return self._resp(method, url, json=self.devices)

    async def h_trigger(self, method, url, data):
        self.triggers.append(data)
        if self.apply_triggers:
            for dev in self.devices:
                if dev["device_id"] == data["device_id"]:
                    for bid, state in zip(data["btnIds"], data["btnStates"], strict=True):
                        dev["buttons"][bid]["switch_state"] = state
        return self._resp(method, url, json={"device": {}})

    async def h_live(self, method, url, data):
        return self._resp(method, url, json=self.energy_live)

    async def h_period(self, method, url, data):
        return self._resp(method, url, json=self.energy_today)

    def register(self, mocker: AiohttpClientMocker, host: str = DEFAULT_HOST) -> None:
        base = re.escape(host)
        mocker.post(re.compile(base + r"/v1/user/login"), side_effect=self.h_login)
        mocker.get(re.compile(base + r"/v1/user/locations"), side_effect=self.h_locations)
        mocker.get(re.compile(base + r"/v1/user/get-things"), side_effect=self.h_get_things)
        mocker.post(re.compile(base + r"/v1/device/trigger"), side_effect=self.h_trigger)
        mocker.get(re.compile(base + r"/v1/user/energy/live"), side_effect=self.h_live)
        mocker.get(re.compile(base + r"/v1/user/energy\?"), side_effect=self.h_period)


def eid(hass, unique_id: str) -> str | None:
    """Return the entity id registered for a unique id."""
    platform = (
        "cover"
        if unique_id.endswith("_shutter")
        else ("sensor" if unique_id.endswith(("_power", "_energy_today")) else "switch")
    )
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


def state_of(hass, unique_id: str):
    """Return the state of the entity with this unique id, or None."""
    entity_id = eid(hass, unique_id)
    return hass.states.get(entity_id) if entity_id else None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components."""
    return


@pytest.fixture
def fake(aioclient_mock: AiohttpClientMocker) -> FakeAutobayt:
    """Return a registered fake backend."""
    backend = FakeAutobayt()
    backend.register(aioclient_mock)
    return backend


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the fake location."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        unique_id=f"{USER_ID}_{LOC_ID}",
        data={
            CONF_HOST: DEFAULT_HOST,
            CONF_TOKEN: TOKEN,
            CONF_USER_ID: USER_ID,
            CONF_LOCATION_ID: LOC_ID,
            CONF_LOCATION_NAME: "Home",
        },
    )


@pytest.fixture
async def setup_integration(hass, fake, config_entry):
    """Set up the integration against the fake backend."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
