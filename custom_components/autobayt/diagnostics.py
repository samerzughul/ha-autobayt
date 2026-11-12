"""Diagnostics with account, location and device identifiers removed."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_LOCATION_ID, CONF_TOKEN, CONF_USER_ID
from .coordinator import AutobaytConfigEntry
from .models import public_fields

TO_REDACT = {
    CONF_TOKEN,
    CONF_USER_ID,
    CONF_LOCATION_ID,
    "email",
    "password",
    "unique_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AutobaytConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics. Devices are numbered instead of named by id."""
    runtime = entry.runtime_data
    known = runtime.devices.data or {}
    secrets = [
        *known,
        str(entry.data.get(CONF_USER_ID) or ""),
        str(entry.data.get(CONF_LOCATION_ID) or ""),
        str(entry.data.get(CONF_TOKEN) or ""),
    ]
    devices = []
    for index, device in enumerate(known.values(), start=1):
        item = asdict(device)
        item["device_id"] = f"device_{index}"
        item["raw"] = public_fields(device.raw, secrets)
        devices.append(item)

    energy: dict[str, Any] | None = None
    if runtime.energy is not None:
        data = runtime.energy.data or {}
        energy = {
            "last_update_success": runtime.energy.last_update_success,
            "live_keys": sorted((data.get("live") or {}).keys()),
            "today_keys": sorted((data.get("today") or {}).keys()),
        }

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "last_update_success": runtime.devices.last_update_success,
        "update_interval": str(runtime.devices.update_interval),
        "consecutive_failures": runtime.devices.failures,
        "devices": devices,
        "energy": energy,
    }
