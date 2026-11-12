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

# On shutter controllers the first two channels drive the motor (up and
# down) and are never exposed as switches. Any further channel is an
# ordinary light relay on the same wall plate.
SHUTTER_MOTOR_CHANNELS = frozenset({0, 1})

# Raw fields kept for diagnostics must not identify the account, home or
# hardware. Keys are matched case-insensitively, so camelCase ids such as
# "deviceId" are caught as well as snake_case ones.
_PRIVATE_KEY = re.compile(
    r"id$|ids$|mac|(^|_)ip|ssid|email|token|user|owner|serial|key|topic|secret|password",
    re.I,
)
REDACTED = "**REDACTED**"


def public_fields(raw: dict[str, Any], secrets: Iterable[str] = ()) -> dict[str, Any]:
    """Return a copy of raw backend fields without identifying values.

    Values under identifying keys are replaced, and any known identifier in
    ``secrets`` is also removed from other text, for example from a topic or
    a URL that embeds a device id.
    """
    known = sorted({s for s in secrets if s and len(s) >= 3}, key=len, reverse=True)

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return public_fields(value, known)
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if isinstance(value, str):
            for secret in known:
                value = value.replace(secret, REDACTED)
        return value

    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "button_id":
            out[key] = value
        elif _PRIVATE_KEY.search(key):
            out[key] = REDACTED
        else:
            out[key] = scrub(value)
    return out


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
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def relay_channels(self) -> list[int]:
        """Return the channels to expose (never a shutter's motor channels)."""
        if self.is_shutter:
            return [b for b in self.buttons if b not in SHUTTER_MOTOR_CHANNELS]
        return list(self.buttons)

    def is_light(self, button_id: int, light_words: Iterable[str]) -> bool:
        """Return True when a channel should be shown as a light."""
        if self.is_shutter:
            return True  # the extra channel on a shutter plate is its light
        button = self.buttons.get(button_id)
        name = (button.name if button else None) or ""
        return any(re.search(rf"\b{re.escape(word)}", name, re.I) for word in light_words if word)

    @property
    def has_power_metering(self) -> bool:
        """Return True for models with a power meter (their names contain PM)."""
        return bool(self.model and "PM" in self.model.upper())


def split_words(text: str) -> list[str]:
    """Split a comma-separated option into words."""
    return [w.strip() for w in str(text).split(",") if w.strip()]


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
        raw=raw,
    )
