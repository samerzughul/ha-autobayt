"""Base entity for Autobayt devices."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AutobaytDeviceCoordinator
from .models import AutobaytDevice


def device_info(device: AutobaytDevice) -> DeviceInfo:
    """Return registry information for one physical device."""
    info = DeviceInfo(
        identifiers={(DOMAIN, device.device_id)},
        manufacturer=MANUFACTURER,
        name=device.name,
        model=device.model,
        sw_version=device.firmware,
    )
    if device.room_name:
        info["suggested_area"] = device.room_name
    return info


class AutobaytEntity(CoordinatorEntity[AutobaytDeviceCoordinator]):
    """An entity that belongs to one Autobayt device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AutobaytDeviceCoordinator, device_id: str) -> None:
        """Bind the entity to a device id."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_device_info = device_info(coordinator.data[device_id])

    @property
    def device(self) -> AutobaytDevice | None:
        """Return the latest data for this entity's device."""
        return (self.coordinator.data or {}).get(self._device_id)

    @property
    def available(self) -> bool:
        """Available while the backend reports the device connected."""
        device = self.device
        return super().available and device is not None and device.available
