"""Cover entities for Autobayt shutter controllers.

An Autobayt shutter is two relay channels on one wall plate: one runs the
motor up (open), the other runs it down (close). Control uses the same
documented trigger request as the switches, with an interlock:

- every move switches the opposite channel off in the same request, listed
  first, so up and down are never on together;
- stop switches both channels off;
- reversing while the shutter moves stops it first and waits a moment.

Position: a position reported by Autobayt is always shown. With a travel time
set in the options, the integration also estimates the position from how long
the motor ran, offers "set position" by running the motor for the right time
and then stopping it, and stops a full open or close once the travel time has
passed. Without a travel time or a reported position the cover offers open,
close and stop only, and its state is assumed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import const
from .api import AutobaytError
from .const import (
    CONF_SWAP_DIRECTION,
    CONF_TRAVEL_TIME,
    DOMAIN,
    SHUTTER_DOWN_CHANNEL,
    SHUTTER_END_MARGIN,
    SHUTTER_UP_CHANNEL,
)
from .coordinator import AutobaytConfigEntry, AutobaytDeviceCoordinator
from .entity import AutobaytEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

OPENING = "opening"
CLOSING = "closing"


def travel_time(entry: AutobaytConfigEntry) -> float | None:
    """Return the configured full travel time in seconds, or None."""
    value = entry.options.get(CONF_TRAVEL_TIME)
    try:
        seconds = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return seconds if seconds and seconds > 0 else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutobaytConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one cover per shutter device."""
    coordinator = entry.runtime_data.devices
    swap = bool(entry.options.get(CONF_SWAP_DIRECTION, False))
    travel = travel_time(entry)
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = []
        for device in (coordinator.data or {}).values():
            if device.is_shutter and device.device_id not in known:
                known.add(device.device_id)
                new.append(AutobaytShutter(coordinator, device.device_id, swap, travel))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AutobaytShutter(AutobaytEntity, CoverEntity):
    """A shutter driven by an up channel and a down channel."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_name = None

    def __init__(
        self,
        coordinator: AutobaytDeviceCoordinator,
        device_id: str,
        swap: bool = False,
        travel: float | None = None,
    ) -> None:
        """Create the cover."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{DOMAIN}_{device_id}_shutter"
        self._up = SHUTTER_DOWN_CHANNEL if swap else SHUTTER_UP_CHANNEL
        self._down = SHUTTER_UP_CHANNEL if swap else SHUTTER_DOWN_CHANNEL
        self._travel = travel
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        if travel:
            features |= CoverEntityFeature.SET_POSITION
        self._attr_supported_features = features
        # A move this entity started and is timing: direction, start time and
        # position at the start (None when unknown).
        self._motion: str | None = None
        self._motion_start = 0.0
        self._motion_from: float | None = None
        self._stop_timer: CALLBACK_TYPE | None = None
        # Position worked out from timed moves, for shutters that report none.
        self._assumed: float | None = None
        # Last direction asked for, to guess open or closed without a position.
        self._last_direction: str | None = None
        self._lock = asyncio.Lock()

    # -- state ------------------------------------------------------------

    def _channel_on(self, button_id: int) -> bool:
        expected = self.coordinator.expected_state(self._device_id, button_id)
        if expected is not None:
            return expected
        device = self.device
        button = device.buttons.get(button_id) if device else None
        return bool(button and button.is_on)

    def _direction(self) -> str | None:
        """Return the way the shutter is moving now, if it is."""
        if self._motion:
            return self._motion
        up, down = self._channel_on(self._up), self._channel_on(self._down)
        if up and not down:
            return OPENING
        if down and not up:
            return CLOSING
        return None

    def _reported(self) -> int | None:
        device = self.device
        return device.position if device else None

    def _estimate(self) -> float | None:
        """Return the position now: from a timed move, from Autobayt, or remembered."""
        if self._motion and self._travel and self._motion_from is not None:
            moved = (time.monotonic() - self._motion_start) / self._travel * 100
            sign = 1 if self._motion == OPENING else -1
            return max(0.0, min(100.0, self._motion_from + sign * moved))
        if (reported := self._reported()) is not None:
            return float(reported)
        return self._assumed

    @property
    def current_cover_position(self) -> int | None:
        """Return 0 (closed) to 100 (open) when the position is known."""
        position = self._estimate()
        return None if position is None else round(position)

    @property
    def is_opening(self) -> bool:
        """Return True while the motor runs up."""
        return self._direction() == OPENING

    @property
    def is_closing(self) -> bool:
        """Return True while the motor runs down."""
        return self._direction() == CLOSING

    @property
    def is_closed(self) -> bool | None:
        """Closed at position 0; without a position, guessed from the last command."""
        position = self.current_cover_position
        if position is not None:
            return position == 0
        if self._last_direction is None:
            return None
        return self._last_direction == CLOSING

    @property
    def assumed_state(self) -> bool:
        """The state is a guess when nothing reports or times the position."""
        return self._travel is None and self._reported() is None

    # -- commands ---------------------------------------------------------

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the shutter fully."""
        async with self._lock:
            await self._async_move(OPENING, self._full_run(), 100.0)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the shutter fully."""
        async with self._lock:
            await self._async_move(CLOSING, self._full_run(), 0.0)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the shutter."""
        async with self._lock:
            await self._async_stop()
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the shutter to a position by timing the motor."""
        target = int(kwargs[ATTR_POSITION])
        async with self._lock:
            if target >= 100:
                await self._async_move(OPENING, self._full_run(), 100.0)
                return
            if target <= 0:
                await self._async_move(CLOSING, self._full_run(), 0.0)
                return
            if self._travel is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="no_travel_time"
                )
            if self._direction() is not None:
                await self._async_stop()
                await asyncio.sleep(const.SHUTTER_REVERSE_PAUSE)
            current = self._estimate()
            if current is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="position_unknown"
                )
            if round(current) == target:
                self.async_write_ha_state()
                return
            direction = OPENING if target > current else CLOSING
            seconds = abs(target - current) / 100 * self._travel
            await self._async_move(direction, seconds, float(target))

    def _full_run(self) -> float | None:
        return self._travel * SHUTTER_END_MARGIN if self._travel else None

    async def _async_move(self, direction: str, seconds: float | None, end: float) -> None:
        """Run the motor one way, stopping first when it runs the other way."""
        moving = self._direction()
        if moving is not None and moving != direction:
            await self._async_stop()
            await asyncio.sleep(const.SHUTTER_REVERSE_PAUSE)
        start = self._estimate()
        self._cancel_timer()
        if direction == OPENING:
            states = {self._down: False, self._up: True}
        else:
            states = {self._up: False, self._down: True}
        await self._send(states)
        self._last_direction = direction
        self._motion = direction if seconds else None
        self._motion_start = time.monotonic()
        self._motion_from = start
        if seconds:

            @callback
            def _done(_now: Any) -> None:
                self._timer_done(end)

            self._stop_timer = async_call_later(self.hass, seconds, _done)
        self.async_write_ha_state()

    async def _async_stop(self) -> None:
        """Switch both channels off and remember where the shutter stopped."""
        position = self._estimate() if self._motion else None
        self._cancel_timer()
        await self._send({self._up: False, self._down: False})
        if position is not None:
            self._assumed = position
        self._motion = None

    @callback
    def _timer_done(self, end: float) -> None:
        self._stop_timer = None
        self.hass.async_create_task(self._async_timed_stop(end), eager_start=True)

    async def _async_timed_stop(self, end: float) -> None:
        async with self._lock:
            if self._motion is None:
                return
            self._motion = None
            try:
                await self._send({self._up: False, self._down: False})
            except HomeAssistantError:
                _LOGGER.warning("Could not stop a shutter after its timed run")
                return
            self._assumed = end
        self.async_write_ha_state()

    async def _send(self, states: dict[int, bool]) -> None:
        try:
            await self.coordinator.async_set_buttons(self._device_id, states)
        except AutobaytError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    def _cancel_timer(self) -> None:
        if self._stop_timer is not None:
            self._stop_timer()
            self._stop_timer = None

    @callback
    def _handle_coordinator_update(self) -> None:
        if not self._motion and (reported := self._reported()) is not None:
            self._assumed = float(reported)
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        """Stop a timed move when the integration unloads, so the motor never runs on."""
        if self._stop_timer is not None:
            self._cancel_timer()
            try:
                await self.coordinator.client.set_buttons(
                    self._device_id, [self._up, self._down], [False, False]
                )
            except AutobaytError:
                _LOGGER.warning("Could not stop a shutter while unloading")
        await super().async_will_remove_from_hass()
