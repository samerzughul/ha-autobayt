"""Light entities: relay channels that drive lights (on/off only)."""

from __future__ import annotations

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .channel import AutobaytChannelEntity
from .coordinator import AutobaytConfigEntry, light_words

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutobaytConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a light for every channel that drives a light."""
    coordinator = entry.runtime_data.devices
    words = light_words(entry)
    known: set[tuple[str, int]] = set()

    @callback
    def _add_new() -> None:
        new = []
        for device in (coordinator.data or {}).values():
            for button_id in device.relay_channels():
                key = (device.device_id, button_id)
                if key in known or not device.is_light(button_id, words):
                    continue
                known.add(key)
                new.append(AutobaytLight(coordinator, device.device_id, button_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AutobaytLight(AutobaytChannelEntity, LightEntity):
    """A relay channel that switches a light on and off."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = frozenset({ColorMode.ONOFF})
