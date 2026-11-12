"""Constants for the Autobayt integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "autobayt"
MANUFACTURER: Final = "Autobayt"

DEFAULT_HOST: Final = "https://api.autobayt.com"

CONF_HOST: Final = "host"
CONF_TOKEN: Final = "token"
CONF_USER_ID: Final = "user_id"
CONF_LOCATION_ID: Final = "location_id"
CONF_LOCATION_NAME: Final = "location_name"

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_SHUTTER_MODELS: Final = "shutter_models"
CONF_ENERGY: Final = "energy"
CONF_TIMEZONE: Final = "energy_timezone"
CONF_LIGHT_WORDS: Final = "light_words"
CONF_SWAP_DIRECTION: Final = "swap_shutter_direction"
CONF_TRAVEL_TIME: Final = "shutter_travel_time"

# A channel whose name contains one of these words is shown as a light;
# other channels stay switches. Owners can change the list in the options.
DEFAULT_LIGHT_WORDS: Final = "light, lamp, dim, spot, led, chandelier"

DEFAULT_SCAN_INTERVAL: Final = 10  # seconds, the owner's explicit choice
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 600
MAX_BACKOFF_INTERVAL: Final = timedelta(minutes=5)
# Entities stay on their last known state through this many failed refreshes
# in a row minus one, so a single dropped request does not flash them
# unavailable.
FAILURES_BEFORE_UNAVAILABLE: Final = 3
# A device missing from this many successful refreshes in a row is removed.
MISSING_POLLS_BEFORE_REMOVAL: Final = 3

# Energy readings go stale on the backend after ~16 minutes and polling does
# not request a fresh measurement, so there is no point asking more often.
ENERGY_LIVE_INTERVAL: Final = timedelta(minutes=3)
ENERGY_PERIOD_INTERVAL: Final = timedelta(minutes=15)
# Used only when neither the options nor Home Assistant name a time zone.
DEFAULT_ENERGY_TIMEZONE: Final = "UTC"

# Follow-up refreshes after a command, in seconds from the command.
COMMAND_REFRESH_DELAYS: Final = (2, 5, 10)
# How long a sent command is shown as the expected state while waiting for
# the backend to report it.
COMMAND_PENDING_TIMEOUT: Final = 15

REQUEST_TIMEOUT: Final = 15

# Shutters: by default channel 0 drives the motor up (open) and channel 1
# drives it down (close). The "swap" option exchanges them.
SHUTTER_UP_CHANNEL: Final = 0
SHUTTER_DOWN_CHANNEL: Final = 1
# Pause between stopping and starting the other way, so the motor never
# reverses while it is still turning.
SHUTTER_REVERSE_PAUSE: Final = 1.0
# A full open or close keeps its channel on this much longer than the travel
# time, so the shutter surely reaches its end before the stop is sent.
SHUTTER_END_MARGIN: Final = 1.1
MAX_TRAVEL_TIME: Final = 300
