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

from .api import AutobaytAuthError, AutobaytClient, AutobaytConnectionError, AutobaytError
from .const import (
    CONF_ENERGY,
    CONF_HOST,
    CONF_LIGHT_WORDS,
    CONF_LOCATION_ID,
    CONF_LOCATION_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SHUTTER_MODELS,
    CONF_TIMEZONE,
    CONF_TOKEN,
    CONF_USER_ID,
    DEFAULT_ENERGY_TIMEZONE,
    DEFAULT_HOST,
    DEFAULT_LIGHT_WORDS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

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


class AutobaytConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in to Autobayt and choose which location to add."""

    VERSION = 1

    def __init__(self) -> None:
        """Start with an empty flow."""
        self._host = DEFAULT_HOST
        self._token: str | None = None
        self._user_id: str | None = None
        self._locations: dict[str, str] = {}

    async def _login(self, user_input: dict[str, Any]) -> tuple[AutobaytClient, dict[str, str]]:
        errors: dict[str, str] = {}
        client = AutobaytClient(async_get_clientsession(self.hass), self._host)
        try:
            self._token, self._user_id = await client.login(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
        except AutobaytAuthError:
            errors["base"] = "invalid_auth"
        except AutobaytConnectionError:
            errors["base"] = "cannot_connect"
        except AutobaytError:
            errors["base"] = "unknown"
        except Exception:
            _LOGGER.exception("Unexpected error while signing in to Autobayt")
            errors["base"] = "unknown"
        return client, errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the Autobayt account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            advanced = user_input.get(SECTION_ADVANCED) or {}
            self._host = str(advanced.get(CONF_HOST) or DEFAULT_HOST).strip().rstrip("/")
            client, errors = await self._login(user_input)
            if not errors:
                try:
                    locations = await client.get_locations(self._user_id)
                except AutobaytError:
                    errors["base"] = "cannot_connect"
                else:
                    self._locations = {
                        str(loc["_id"]): str(loc.get("name") or "Home") for loc in locations
                    }
                    if not self._locations:
                        return self.async_abort(reason="no_locations")
                    if len(self._locations) == 1:
                        return await self._create(next(iter(self._locations)))
                    return await self.async_step_location()

        schema = {
            **_CREDENTIALS,
            vol.Required(SECTION_ADVANCED): section(
                vol.Schema(
                    {
                        vol.Required(CONF_HOST, default=DEFAULT_HOST): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.URL)
                        )
                    }
                ),
                {"collapsed": True},
            ),
        }
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
            _client, errors = await self._login(user_input)
            if not errors:
                if self._user_id != entry.data[CONF_USER_ID]:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: self._token}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(_CREDENTIALS),
            errors=errors,
            description_placeholders={"location": entry.title},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> AutobaytOptionsFlow:
        """Return the options flow."""
        return AutobaytOptionsFlow()


class AutobaytOptionsFlow(OptionsFlowWithReload):
    """Polling interval, shutter models and energy sensors."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
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
                vol.Optional(
                    CONF_LIGHT_WORDS,
                    default=options.get(CONF_LIGHT_WORDS, DEFAULT_LIGHT_WORDS),
                ): TextSelector(),
                vol.Required(CONF_ENERGY, default=options.get(CONF_ENERGY, True)): bool,
                vol.Required(
                    CONF_TIMEZONE,
                    default=options.get(CONF_TIMEZONE, DEFAULT_ENERGY_TIMEZONE),
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
