"""Switch entities: relay channels that are not lights."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    """Add a switch for every channel that is not a light."""
    coordinator = entry.runtime_data.devices
    words = light_words(entry)
    known: set[tuple[str, int]] = set()

    @callback
    def _add_new() -> None:
        new = []
        for device in (coordinator.data or {}).values():
            for button_id in device.relay_channels():
                key = (device.device_id, button_id)
                if key in known or device.is_light(button_id, words):
                    continue
                known.add(key)
                new.append(AutobaytSwitch(coordinator, device.device_id, button_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AutobaytSwitch(AutobaytChannelEntity, SwitchEntity):
    """A relay channel that is not a light, for example a boiler."""
