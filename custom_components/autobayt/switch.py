"""Switch entities: one per relay channel."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AutobaytError
from .const import DOMAIN
from .coordinator import AutobaytConfigEntry, AutobaytDeviceCoordinator
from .entity import AutobaytEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutobaytConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a switch for every relay channel of every non-shutter device."""
    coordinator = entry.runtime_data.devices
    known: set[tuple[str, int]] = set()

    @callback
    def _add_new() -> None:
        new = []
        for device in (coordinator.data or {}).values():
            if device.is_shutter:
                continue
            for button_id in device.buttons:
                key = (device.device_id, button_id)
                if key not in known:
                    known.add(key)
                    new.append(AutobaytSwitch(coordinator, device.device_id, button_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AutobaytSwitch(AutobaytEntity, SwitchEntity):
    """One relay channel of an Autobayt switch."""

    def __init__(
        self, coordinator: AutobaytDeviceCoordinator, device_id: str, button_id: int
    ) -> None:
        """Create the switch for one channel."""
        super().__init__(coordinator, device_id)
        self._button_id = button_id
        self._attr_unique_id = f"{DOMAIN}_{device_id}_{button_id}"
        self._attr_name = self._channel_name()

    def _channel_name(self) -> str | None:
        device = self.device
        if device is None:
            return None
        button = device.buttons.get(self._button_id)
        label = button.name if button else None
        if len(device.buttons) == 1 and (not label or label == device.name):
            return None  # the switch takes the device's name
        return label or f"Channel {self._button_id + 1}"

    @property
    def available(self) -> bool:
        """Unavailable when the channel disappears from the device."""
        device = self.device
        return super().available and device is not None and self._button_id in device.buttons

    @property
    def is_on(self) -> bool | None:
        """Return the reported state, or the commanded one while it settles."""
        expected = self.coordinator.expected_state(self._device_id, self._button_id)
        if expected is not None:
            return expected
        device = self.device
        button = device.buttons.get(self._button_id) if device else None
        return button.is_on if button else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the channel on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the channel off."""
        await self._async_set(False)

    async def _async_set(self, state: bool) -> None:
        try:
            await self.coordinator.async_set_button(self._device_id, self._button_id, state)
        except AutobaytError as err:
            raise HomeAssistantError(f"Autobayt did not accept the command: {err}") from err

    @callback
    def _handle_coordinator_update(self) -> None:
        name = self._channel_name()
        if name != self._attr_name and self.registry_entry and not self.registry_entry.name:
            self._attr_name = name
        super()._handle_coordinator_update()
