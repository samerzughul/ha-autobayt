"""Coordinators that poll the Autobayt backend."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AutobaytAuthError,
    AutobaytClient,
    AutobaytError,
    AutobaytRateLimitError,
)
from .const import (
    COMMAND_PENDING_TIMEOUT,
    COMMAND_REFRESH_DELAYS,
    CONF_ENERGY,
    CONF_LIGHT_WORDS,
    CONF_LOCATION_ID,
    CONF_SCAN_INTERVAL,
    CONF_SHUTTER_MODELS,
    CONF_TIMEZONE,
    CONF_USER_ID,
    DEFAULT_ENERGY_TIMEZONE,
    DEFAULT_LIGHT_WORDS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENERGY_LIVE_INTERVAL,
    ENERGY_PERIOD_INTERVAL,
    FAILURES_BEFORE_UNAVAILABLE,
    MAX_BACKOFF_INTERVAL,
    MISSING_POLLS_BEFORE_REMOVAL,
)
from .models import AutobaytDevice, parse_device, split_words

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class AutobaytRuntimeData:
    """Objects shared by the platforms of one config entry."""

    client: AutobaytClient
    devices: AutobaytDeviceCoordinator
    energy: AutobaytEnergyCoordinator | None


type AutobaytConfigEntry = ConfigEntry[AutobaytRuntimeData]


def shutter_models(entry: ConfigEntry) -> list[str]:
    """Return the owner's extra shutter model names from the options."""
    return split_words(entry.options.get(CONF_SHUTTER_MODELS, ""))


def light_words(entry: ConfigEntry) -> list[str]:
    """Return the words that mark a channel as a light."""
    return split_words(entry.options.get(CONF_LIGHT_WORDS, DEFAULT_LIGHT_WORDS))


def _auth_failed() -> ConfigEntryAuthFailed:
    return ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="auth_failed")


def energy_timezone(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the time zone that decides when the energy day starts."""
    return entry.options.get(CONF_TIMEZONE) or hass.config.time_zone or DEFAULT_ENERGY_TIMEZONE


def _backoff(base: timedelta, failures: int, retry_after: float | None) -> timedelta:
    delay = base * (2 ** min(failures, 6))
    if retry_after:
        delay = max(delay, timedelta(seconds=retry_after))
    return min(delay, max(MAX_BACKOFF_INTERVAL, base))


class AutobaytDeviceCoordinator(DataUpdateCoordinator[dict[str, AutobaytDevice]]):
    """Polls get-things once per cycle for the whole location."""

    config_entry: AutobaytConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: AutobaytClient) -> None:
        """Set up the coordinator with the interval from the options."""
        self._base_interval = timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} devices",
            update_interval=self._base_interval,
        )
        self.client = client
        self._user_id: str = entry.data[CONF_USER_ID]
        self._location_id: str = entry.data[CONF_LOCATION_ID]
        self._shutter_models = shutter_models(entry)
        self._failures = 0
        self._missing: dict[str, int] = {}
        # (device_id, button_id) -> (wanted state, deadline as monotonic time)
        self._pending: dict[tuple[str, int], tuple[bool, float]] = {}
        self._followups: list[CALLBACK_TYPE] = []

    @property
    def failures(self) -> int:
        """Return how many refreshes in a row have failed."""
        return self._failures

    async def _async_update_data(self) -> dict[str, AutobaytDevice]:
        try:
            raw = await self.client.get_things(self._user_id, self._location_id)
        except AutobaytAuthError as err:
            raise _auth_failed() from err
        except AutobaytError as err:
            retry_after = err.retry_after if isinstance(err, AutobaytRateLimitError) else None
            self._slow_down(retry_after)
            # A cloud service drops the odd request. Keep showing the last
            # known states through a short blip instead of flashing every
            # entity unavailable; after a few failures in a row, give up.
            if self.data is not None and self._failures < FAILURES_BEFORE_UNAVAILABLE:
                _LOGGER.debug("Autobayt refresh failed (%s), keeping the last states", err)
                return self.data
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        if self._failures:
            self._failures = 0
            self.update_interval = self._base_interval

        devices = {}
        for item in raw:
            device = parse_device(item, self._shutter_models)
            devices[device.device_id] = device
        self._settle_pending(devices)
        if devices:
            self._remove_stale_devices(devices)
        return devices

    def _remove_stale_devices(self, devices: dict[str, AutobaytDevice]) -> None:
        """Remove devices Autobayt has stopped reporting for several polls.

        Waiting a few polls keeps a device that briefly drops out of the list
        (and its entity settings) from being removed.
        """
        dev_reg = dr.async_get(self.hass)
        for entry in dr.async_entries_for_config_entry(dev_reg, self.config_entry.entry_id):
            device_id = next(
                (ident for domain, ident in entry.identifiers if domain == DOMAIN), None
            )
            if device_id is None or device_id.startswith("location_"):
                continue
            if device_id in devices:
                self._missing.pop(device_id, None)
                continue
            self._missing[device_id] = self._missing.get(device_id, 0) + 1
            if self._missing[device_id] >= MISSING_POLLS_BEFORE_REMOVAL:
                _LOGGER.debug("Removing a device Autobayt no longer reports")
                dev_reg.async_update_device(
                    entry.id, remove_config_entry_id=self.config_entry.entry_id
                )
                self._missing.pop(device_id, None)

    def _slow_down(self, retry_after: float | None) -> None:
        self._failures += 1
        self.update_interval = _backoff(self._base_interval, self._failures, retry_after)
        _LOGGER.debug(
            "Autobayt update failed %s time(s), next try in %s",
            self._failures,
            self.update_interval,
        )

    # -- commands --------------------------------------------------------

    def expected_state(self, device_id: str, button_id: int) -> bool | None:
        """Return the state a recent command is waiting for, if any."""
        pending = self._pending.get((device_id, button_id))
        if pending is None:
            return None
        wanted, deadline = pending
        if time.monotonic() > deadline:
            self._pending.pop((device_id, button_id), None)
            return None
        return wanted

    def _settle_pending(self, devices: dict[str, AutobaytDevice]) -> None:
        now = time.monotonic()
        for key, (wanted, deadline) in list(self._pending.items()):
            device = devices.get(key[0])
            button = device.buttons.get(key[1]) if device else None
            if now > deadline or (button is not None and button.is_on == wanted):
                del self._pending[key]
        if not self._pending:
            self.cancel_followups()

    async def async_set_button(self, device_id: str, button_id: int, state: bool) -> None:
        """Send one relay command and watch for the reported state."""
        try:
            await self.client.set_buttons(device_id, [button_id], [state])
        except AutobaytAuthError:
            self.config_entry.async_start_reauth(self.hass)
            raise
        self._pending[(device_id, button_id)] = (
            state,
            time.monotonic() + COMMAND_PENDING_TIMEOUT,
        )
        self.async_update_listeners()
        self._schedule_followups()

    def _schedule_followups(self) -> None:
        self.cancel_followups()

        @callback
        def _refresh(_now: Any) -> None:
            if self._pending:
                self.hass.async_create_task(self.async_refresh())

        self._followups = [
            async_call_later(self.hass, delay, _refresh) for delay in COMMAND_REFRESH_DELAYS
        ]

    def cancel_followups(self) -> None:
        """Cancel pending follow-up refreshes."""
        for cancel in self._followups:
            cancel()
        self._followups = []

    async def async_shutdown(self) -> None:
        """Cancel follow-ups when the entry unloads."""
        self.cancel_followups()
        await super().async_shutdown()


class AutobaytEnergyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls live power every few minutes and today's consumption less often."""

    config_entry: AutobaytConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: AutobaytClient) -> None:
        """Set up the energy coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} energy",
            update_interval=ENERGY_LIVE_INTERVAL,
        )
        self.client = client
        self._location_id: str = entry.data[CONF_LOCATION_ID]
        self._timezone: str = energy_timezone(hass, entry)
        self._period: dict[str, Any] | None = None
        self._period_fetched: float = 0.0
        self._period_date: str | None = None
        self._failures = 0

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            live = await self.client.get_energy_live(self._location_id)
            today = dt_util.now(dt_util.get_time_zone(self._timezone)).date().isoformat()
            stale = (
                time.monotonic() - self._period_fetched >= ENERGY_PERIOD_INTERVAL.total_seconds()
            )
            if self._period is None or stale or today != self._period_date:
                self._period = await self.client.get_energy_period(
                    self._location_id, "day", today, self._timezone
                )
                self._period_fetched = time.monotonic()
                self._period_date = today
        except AutobaytAuthError as err:
            raise _auth_failed() from err
        except AutobaytError as err:
            self._failures += 1
            retry = err.retry_after if isinstance(err, AutobaytRateLimitError) else None
            self.update_interval = _backoff(ENERGY_LIVE_INTERVAL, self._failures, retry)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="energy_update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        if self._failures:
            self._failures = 0
            self.update_interval = ENERGY_LIVE_INTERVAL
        return {"live": live, "today": self._period}


def energy_enabled(entry: ConfigEntry) -> bool:
    """Return True when the owner wants energy sensors."""
    return bool(entry.options.get(CONF_ENERGY, True))
