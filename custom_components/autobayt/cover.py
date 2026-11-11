"""Cover entities for Autobayt shutter controllers.

Shutters are read-only for now: they show the position Autobayt reports, but
open, close, stop and set-position are not offered. Autobayt's documented
relay command carries only on/off states for each channel, not a position or
a stop, and driving a shutter motor's channels as plain relays is unsafe.
Once Autobayt publishes a shutter control contract, implement
AutobaytClient.shutter_command and set SHUTTER_CONTROL_SUPPORTED to True.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AutobaytError
from .const import DOMAIN
from .coordinator import AutobaytConfigEntry, AutobaytDeviceCoordinator
from .entity import AutobaytEntity

PARALLEL_UPDATES = 1

# Flip once AutobaytClient.shutter_command talks to a real endpoint.
SHUTTER_CONTROL_SUPPORTED = False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutobaytConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one cover per shutter device."""
    coordinator = entry.runtime_data.devices
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = []
        for device in (coordinator.data or {}).values():
            if device.is_shutter and device.device_id not in known:
                known.add(device.device_id)
                new.append(AutobaytShutter(coordinator, device.device_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AutobaytShutter(AutobaytEntity, CoverEntity):
    """A shutter, shown read-only until control is available."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_name = None

    def __init__(self, coordinator: AutobaytDeviceCoordinator, device_id: str) -> None:
        """Create the cover."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{DOMAIN}_{device_id}_shutter"
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
            if SHUTTER_CONTROL_SUPPORTED
            else CoverEntityFeature(0)
        )

    @property
    def current_cover_position(self) -> int | None:
        """Return 0 (closed) to 100 (open) when Autobayt reports a position."""
        device = self.device
        return device.position if device else None

    @property
    def is_closed(self) -> bool | None:
        """Closed at position 0; unknown without a position."""
        position = self.current_cover_position
        return None if position is None else position == 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the shutter."""
        await self._async_command("open")

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the shutter."""
        await self._async_command("close")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the shutter."""
        await self._async_command("stop")

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the shutter to a position."""
        await self._async_command("position", int(kwargs[ATTR_POSITION]))

    async def _async_command(self, action: str, position: int | None = None) -> None:
        try:
            await self.coordinator.client.shutter_command(self._device_id, action, position)
        except AutobaytError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
