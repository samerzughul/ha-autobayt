"""Parsed Autobayt devices."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import re
from typing import Any

# Words in a model name that mark a shutter or curtain motor controller.
# Owners can add exact model names in the integration options when their
# model does not match.
# Field presence is not used: a device-level "perc" also appears during
# firmware updates on ordinary switches.
_SHUTTER_PATTERN = re.compile(r"shutter|curtain|blind|roller|^(SH|CT|CR|CUR)\d", re.I)


@dataclass(slots=True)
class AutobaytButton:
    """One relay channel."""

    button_id: int
    name: str | None
    is_on: bool | None


@dataclass(slots=True)
class AutobaytDevice:
    """One physical Autobayt device."""

    device_id: str
    name: str
    model: str | None
    firmware: str | None
    room_name: str | None
    available: bool
    is_shutter: bool
    position: int | None
    buttons: dict[int, AutobaytButton] = field(default_factory=dict)

    @property
    def has_power_metering(self) -> bool:
        """Return True for models with a power meter (their names contain PM)."""
        return bool(self.model and "PM" in self.model.upper())


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("true", "false", "on", "off", "1", "0"):
        return value.lower() in ("true", "on", "1")
    return None


def _as_position(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        pos = round(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, pos))


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def is_shutter(raw: dict[str, Any], extra_models: Iterable[str] = ()) -> bool:
    """Decide whether a device is a shutter controller."""
    model = _clean(raw.get("model_name")) or ""
    extra = {m.strip().upper() for m in extra_models if m.strip()}
    if model.upper() in extra:
        return True
    if model and _SHUTTER_PATTERN.search(model):
        return True
    for key in ("device_type", "type", "category"):
        value = _clean(raw.get(key))
        if value and _SHUTTER_PATTERN.search(value):
            return True
    return False


def _shutter_position(raw: dict[str, Any]) -> int | None:
    if (pos := _as_position(raw.get("perc"))) is not None:
        return pos
    for button in raw.get("buttons") or []:
        if isinstance(button, dict) and (pos := _as_position(button.get("perc"))) is not None:
            return pos
    return None


def parse_device(raw: dict[str, Any], extra_shutter_models: Iterable[str] = ()) -> AutobaytDevice:
    """Turn one get-things entry into an AutobaytDevice."""
    device_id = str(raw["device_id"])
    buttons: dict[int, AutobaytButton] = {}
    for item in raw.get("buttons") or []:
        if not isinstance(item, dict):
            continue
        try:
            button_id = int(item["button_id"])
        except (KeyError, TypeError, ValueError):
            continue
        buttons[button_id] = AutobaytButton(
            button_id=button_id,
            name=_clean(item.get("name")),
            is_on=_as_bool(item.get("switch_state")),
        )
    shutter = is_shutter(raw, extra_shutter_models)
    return AutobaytDevice(
        device_id=device_id,
        name=_clean(raw.get("name")) or _clean(raw.get("model_name")) or "Autobayt device",
        model=_clean(raw.get("model_name")),
        firmware=_clean(raw.get("firmware_version")),
        room_name=_clean(raw.get("room_name")),
        available=raw.get("connection_status") is not False,
        is_shutter=shutter,
        position=_shutter_position(raw) if shutter else None,
        buttons=buttons,
    )
