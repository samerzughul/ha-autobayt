"""Shared behaviour of relay channel entities (switches and lights)."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import AutobaytError
from .const import DOMAIN
from .coordinator import AutobaytDeviceCoordinator
from .entity import AutobaytEntity

_GENERIC = re.compile(r"^(button|channel)\s*\d*$", re.I)


class AutobaytChannelEntity(AutobaytEntity):
    """One relay channel of an Autobayt device."""

    def __init__(
        self, coordinator: AutobaytDeviceCoordinator, device_id: str, button_id: int
    ) -> None:
        """Create the entity for one channel."""
        super().__init__(coordinator, device_id)
        self._button_id = button_id
        self._attr_unique_id = f"{DOMAIN}_{device_id}_{button_id}"
        self._name_spec: tuple[str, Any] | None = None
        self._apply_name()

    def _wanted_name(self) -> tuple[str, Any]:  # noqa: PLR0911 - one return per naming rule
        """Return ("name", text or None) or ("key", (translation key, placeholders)).

        Names typed in the Autobayt app are used as they are; names this
        integration makes up are translated.
        """
        device = self.device
        if device is None:
            return ("name", None)
        button = device.buttons.get(self._button_id)
        label = button.name if button else None
        if device.is_shutter:
            # Make it obvious that this is the plate's light, not the shutter.
            if not label or _GENERIC.match(label):
                return ("key", ("plate_light", {}))
            if "light" in label.lower():
                return ("name", label)
            return ("key", ("named_plate_light", {"label": label}))
        single = len(device.relay_channels()) == 1
        if single and (not label or label == device.name):
            return ("name", None)  # the entity takes the device's name
        if label:
            return ("name", label)
        return ("key", ("channel", {"number": str(self._button_id + 1)}))

    def _apply_name(self) -> bool:
        """Set the name attributes; return True when they changed."""
        spec = self._wanted_name()
        if spec == self._name_spec:
            return False
        self._name_spec = spec
        kind, value = spec
        if kind == "name":
            self._attr_name = value
        else:
            if hasattr(self, "_attr_name"):
                del self._attr_name
            self._attr_translation_key, self._attr_translation_placeholders = value
            # Home Assistant caches the lookup key derived from translation_key.
            self.__dict__.pop("_name_translation_key", None)
        return True

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
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    @callback
    def _handle_coordinator_update(self) -> None:
        # Follow renames made in the Autobayt app; a name set in Home
        # Assistant always wins over this one anyway.
        self._apply_name()
        super()._handle_coordinator_update()
