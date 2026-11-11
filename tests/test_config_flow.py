"""Config, re-auth and options flows."""

from __future__ import annotations

import pytest

from custom_components.autobayt.const import (
    CONF_ENERGY,
    CONF_LIGHT_WORDS,
    CONF_LOCATION_ID,
    CONF_SCAN_INTERVAL,
    CONF_SHUTTER_MODELS,
    CONF_TIMEZONE,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType

from .conftest import EMAIL, LOC_ID, OTHER_LOC_ID, PASSWORD, TOKEN, USER_ID, state_of

CREDS = {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
SETUP = {**CREDS, "advanced": {"host": "https://api.autobayt.com"}}


async def _start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_single_location_creates_entry_without_password(hass, fake):
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETUP)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    data = result["data"]
    assert data[CONF_TOKEN] == TOKEN
    assert data[CONF_USER_ID] == USER_ID
    assert data[CONF_LOCATION_ID] == LOC_ID
    assert CONF_PASSWORD not in data
    assert CONF_EMAIL not in data


async def test_multiple_locations_ask_which(hass, fake):
    fake.locations.append({"_id": OTHER_LOC_ID, "name": "Farm", "user_id": USER_ID})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETUP)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "location"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_ID: OTHER_LOC_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Farm"
    assert result["data"][CONF_LOCATION_ID] == OTHER_LOC_ID


@pytest.mark.parametrize("code", [111, 112])
async def test_wrong_credentials(hass, fake, code):
    fake.login_error_code = code
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETUP)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_server_down(hass, aioclient_mock):
    aioclient_mock.post("https://api.autobayt.com/v1/user/login", status=502)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETUP)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_no_locations(hass, fake):
    fake.locations.clear()
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETUP)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_locations"


async def test_already_configured(hass, fake, config_entry):
    config_entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETUP)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_stores_new_token(hass, fake, config_entry):
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_TOKEN: "old"}
    )
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_TOKEN] == TOKEN


async def test_reauth_other_account_refused(hass, fake, config_entry):
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_USER_ID: "f" * 24}
    )
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_options(hass, setup_integration):
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 30,
            CONF_SHUTTER_MODELS: "SW200LV1",
            CONF_ENERGY: False,
            CONF_TIMEZONE: "Asia/Amman",
            CONF_LIGHT_WORDS: "light",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_SCAN_INTERVAL] == 30
    assert entry.runtime_data.devices.update_interval.total_seconds() == 30
    # The named model is now a shutter: its channels are no longer switches.
    old = state_of(hass, "autobayt_sw-1_0")
    assert old is None or old.state == "unavailable"
    assert state_of(hass, "autobayt_sw-1_shutter") is not None
    assert entry.runtime_data.energy is None
