"""Energy sensors for Autobayt power-metering (PM) devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_LOCATION_ID, CONF_LOCATION_NAME, DOMAIN, MANUFACTURER
from .coordinator import AutobaytConfigEntry, AutobaytEnergyCoordinator
from .entity import device_info

PARALLEL_UPDATES = 0

# Quality states that must not be shown as a confirmed number.
_UNCONFIRMED = {"no_data", "unclassified", "incomplete", "offline", "stale", "inactive", "future"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _usable(item: dict[str, Any] | None, key: str) -> float | None:
    """Return item[key] unless the item's quality says it is not a real value."""
    if not isinstance(item, dict):
        return None
    if str(item.get("status", "")).lower() in _UNCONFIRMED:
        return None
    return _number(item.get(key))


def _by_device(payload: dict[str, Any] | None, device_id: str) -> dict[str, Any] | None:
    for item in (payload or {}).get("by_device") or []:
        if isinstance(item, dict) and str(item.get("device_id")) == device_id:
            return item
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutobaytConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add power and today's-energy sensors when energy is enabled."""
    energy = entry.runtime_data.energy
    if energy is None:
        return
    location_id = entry.data[CONF_LOCATION_ID]
    location = DeviceInfo(
        identifiers={(DOMAIN, f"location_{location_id}")},
        manufacturer=MANUFACTURER,
        name=entry.data.get(CONF_LOCATION_NAME) or entry.title,
        model="Location",
    )
    entities: list[SensorEntity] = [
        LocationPowerSensor(energy, location, f"{DOMAIN}_{location_id}_power"),
        LocationEnergySensor(energy, location, f"{DOMAIN}_{location_id}_energy_today"),
    ]
    for device in entry.runtime_data.devices.data.values():
        if device.has_power_metering:
            info = device_info(device)
            entities.append(DevicePowerSensor(energy, info, device.device_id))
            entities.append(DeviceEnergySensor(energy, info, device.device_id))
    async_add_entities(entities)


class _EnergyBase(CoordinatorEntity[AutobaytEnergyCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AutobaytEnergyCoordinator, info: DeviceInfo, unique_id: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = info
        self._attr_unique_id = unique_id

    def _value(self) -> float | None:
        raise NotImplementedError

    @property
    def native_value(self) -> float | None:
        """Return the reading, or None when Autobayt has no usable value."""
        return self._value()

    @property
    def available(self) -> bool:
        """Unavailable when there is no usable reading (never shown as zero)."""
        return super().available and self._value() is not None


class _PowerBase(_EnergyBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_translation_key = "power"


class _EnergyTodayBase(_EnergyBase):
    # A daily total that starts again at local midnight.
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_translation_key = "energy_today"


class LocationPowerSensor(_PowerBase):
    """Current power of the whole location."""

    def _value(self) -> float | None:
        return _usable((self.coordinator.data or {}).get("live"), "power_w")


class LocationEnergySensor(_EnergyTodayBase):
    """Consumption of the whole location today."""

    def _value(self) -> float | None:
        return _usable((self.coordinator.data or {}).get("today"), "consumption_kwh")


class DevicePowerSensor(_PowerBase):
    """Current power of one device."""

    def __init__(
        self, coordinator: AutobaytEnergyCoordinator, info: DeviceInfo, device_id: str
    ) -> None:
        """Bind to one device."""
        super().__init__(coordinator, info, f"{DOMAIN}_{device_id}_power")
        self._device_id = device_id

    def _value(self) -> float | None:
        live = (self.coordinator.data or {}).get("live")
        return _usable(_by_device(live, self._device_id), "power_w")


class DeviceEnergySensor(_EnergyTodayBase):
    """Consumption of one device today."""

    def __init__(
        self, coordinator: AutobaytEnergyCoordinator, info: DeviceInfo, device_id: str
    ) -> None:
        """Bind to one device."""
        super().__init__(coordinator, info, f"{DOMAIN}_{device_id}_energy_today")
        self._device_id = device_id

    def _value(self) -> float | None:
        today = (self.coordinator.data or {}).get("today")
        return _usable(_by_device(today, self._device_id), "consumption_kwh")
