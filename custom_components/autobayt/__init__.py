"""The Autobayt integration: Autobayt Wi-Fi switches and shutters via the Autobayt cloud."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AutobaytClient
from .const import CONF_HOST, CONF_TOKEN, DEFAULT_HOST, DOMAIN
from .coordinator import (
    AutobaytConfigEntry,
    AutobaytDeviceCoordinator,
    AutobaytEnergyCoordinator,
    AutobaytRuntimeData,
    energy_enabled,
)

PLATFORMS: list[Platform] = [Platform.COVER, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: AutobaytConfigEntry) -> bool:
    """Set up one Autobayt location."""
    client = AutobaytClient(
        async_get_clientsession(hass),
        entry.data.get(CONF_HOST, DEFAULT_HOST),
        entry.data[CONF_TOKEN],
    )
    devices = AutobaytDeviceCoordinator(hass, entry, client)
    await devices.async_config_entry_first_refresh()

    energy: AutobaytEnergyCoordinator | None = None
    if energy_enabled(entry) and any(d.has_power_metering for d in devices.data.values()):
        energy = AutobaytEnergyCoordinator(hass, entry, client)
        # Energy is optional: a failure here must not block the switches.
        await energy.async_refresh()

    entry.runtime_data = AutobaytRuntimeData(client=client, devices=devices, energy=energy)
    entry.async_on_unload(devices.cancel_followups)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AutobaytConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AutobaytConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing a device only once Autobayt no longer reports it."""
    known = entry.runtime_data.devices.data or {}
    return not any(domain == DOMAIN and ident in known for domain, ident in device.identifiers)
