"""Config flow for Autobayt: sign in, pick a location, keep only the token."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import AutobaytAuthError, AutobaytClient, AutobaytConnectionError, AutobaytError
from .const import (
    CONF_ENERGY,
    CONF_HOST,
    CONF_LIGHT_WORDS,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SHUTTER_MODELS,
    CONF_SWAP_DIRECTION,
    CONF_TIMEZONE,
    CONF_TOKEN,
    CONF_TRAVEL_TIME,
    CONF_USER_ID,
    DEFAULT_HOST,
    DEFAULT_LIGHT_WORDS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MAX_TRAVEL_TIME,
    MIN_SCAN_INTERVAL,
)
from .coordinator import energy_timezone

_LOGGER = logging.getLogger(__name__)

SECTION_ADVANCED = "advanced"

_CREDENTIALS = {
    vol.Required(CONF_EMAIL): TextSelector(
        TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
    ),
    vol.Required(CONF_PASSWORD): TextSelector(
        TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
    ),
}


def _host_section(default: str) -> section:
    return section(
        vol.Schema(
            {
                vol.Required(CONF_HOST, default=default): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                )
            }
        ),
        {"collapsed": True},
    )


def _host_from(user_input: dict[str, Any], default: str) -> str:
    advanced = user_input.get(SECTION_ADVANCED) or {}
    return str(advanced.get(CONF_HOST) or default).strip().rstrip("/")


class AutobaytConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in to Autobayt and choose which location to add."""

    VERSION = 1

    def __init__(self) -> None:
        """Start with an empty flow."""
        self._host = DEFAULT_HOST
        self._token: str | None = None
        self._user_id: str | None = None
        self._locations: dict[str, str] = {}

    async def _sign_in(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Sign in and read the account's locations. Return form errors."""
        client = AutobaytClient(async_get_clientsession(self.hass), self._host)
        try:
            self._token, self._user_id = await client.login(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            locations = await client.get_locations(self._user_id)
        except AutobaytAuthError:
            return {"base": "invalid_auth"}
        except AutobaytConnectionError:
            return {"base": "cannot_connect"}
        except AutobaytError:
            return {"base": "unknown"}
        except Exception:
            _LOGGER.exception("Unexpected error while signing in to Autobayt")
            return {"base": "unknown"}
        self._locations = {str(loc["_id"]): str(loc.get("name") or "Home") for loc in locations}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the Autobayt account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = _host_from(user_input, DEFAULT_HOST)
            errors = await self._sign_in(user_input)
            if not errors:
                if not self._locations:
                    return self.async_abort(reason="no_locations")
                if len(self._locations) == 1:
                    return await self._create(next(iter(self._locations)))
                return await self.async_step_location()

        schema = {**_CREDENTIALS, vol.Required(SECTION_ADVANCED): _host_section(DEFAULT_HOST)}
        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the account's locations."""
        if user_input is not None:
            return await self._create(user_input[CONF_LOCATION_ID])
        options = [SelectOptionDict(value=k, label=v) for k, v in self._locations.items()]
        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCATION_ID): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def _create(self, location_id: str) -> ConfigFlowResult:
        await self.async_set_unique_id(f"{self._user_id}_{location_id}")
        self._abort_if_unique_id_configured()
        name = self._locations.get(location_id, "Autobayt")
        return self.async_create_entry(
            title=name,
            data={
                CONF_HOST: self._host,
                CONF_TOKEN: self._token,
                CONF_USER_ID: self._user_id,
                CONF_LOCATION_ID: location_id,
                CONF_LOCATION_NAME: name,
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after Autobayt rejected the token."""
        self._host = entry_data.get(CONF_HOST, DEFAULT_HOST)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in again and store the new token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            errors = await self._sign_in(user_input)
            if not errors:
                await self.async_set_unique_id(f"{self._user_id}_{entry.data[CONF_LOCATION_ID]}")
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: self._token}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(_CREDENTIALS),
            errors=errors,
            description_placeholders={"location": entry.title},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in again, optionally to another server address, for the same home."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        current_host = entry.data.get(CONF_HOST, DEFAULT_HOST)
        if user_input is not None:
            self._host = _host_from(user_input, current_host)
            errors = await self._sign_in(user_input)
            if not errors:
                location_id = entry.data[CONF_LOCATION_ID]
                await self.async_set_unique_id(f"{self._user_id}_{location_id}")
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                if location_id not in self._locations:
                    return self.async_abort(reason="location_missing")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_HOST: self._host,
                        CONF_TOKEN: self._token,
                        CONF_LOCATION_NAME: self._locations[location_id],
                    },
                )
        schema = {**_CREDENTIALS, vol.Required(SECTION_ADVANCED): _host_section(current_host)}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"location": entry.title},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> AutobaytOptionsFlow:
        """Return the options flow."""
        return AutobaytOptionsFlow()


class AutobaytOptionsFlow(OptionsFlowWithReload):
    """Polling interval, shutter models, light words and energy sensors."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            if user_input.get(CONF_TRAVEL_TIME) in (None, "", 0):
                user_input.pop(CONF_TRAVEL_TIME, None)
            timezone = str(user_input.get(CONF_TIMEZONE, "")).strip()
            user_input[CONF_TIMEZONE] = timezone
            if dt_util.get_time_zone(timezone) is None:
                errors[CONF_TIMEZONE] = "invalid_timezone"
            else:
                return self.async_create_entry(data=user_input)
        options = {**self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_SHUTTER_MODELS, default=options.get(CONF_SHUTTER_MODELS, "")
                ): TextSelector(),
                vol.Required(
                    CONF_SWAP_DIRECTION, default=options.get(CONF_SWAP_DIRECTION, False)
                ): bool,
                vol.Optional(
                    CONF_TRAVEL_TIME,
                    description={"suggested_value": options.get(CONF_TRAVEL_TIME)},
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=MAX_TRAVEL_TIME,
                        step=0.5,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_LIGHT_WORDS,
                    default=options.get(CONF_LIGHT_WORDS, DEFAULT_LIGHT_WORDS),
                ): TextSelector(),
                vol.Required(CONF_ENERGY, default=options.get(CONF_ENERGY, True)): bool,
                vol.Required(
                    CONF_TIMEZONE,
                    default=options.get(CONF_TIMEZONE)
                    or energy_timezone(self.hass, self.config_entry),
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
