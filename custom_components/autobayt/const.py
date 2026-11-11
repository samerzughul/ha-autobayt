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

DEFAULT_SCAN_INTERVAL: Final = 10  # seconds, the owner's explicit choice
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 600
MAX_BACKOFF_INTERVAL: Final = timedelta(minutes=5)

# Energy readings go stale on the backend after ~16 minutes and polling does
# not request a fresh measurement, so there is no point asking more often.
ENERGY_LIVE_INTERVAL: Final = timedelta(minutes=3)
ENERGY_PERIOD_INTERVAL: Final = timedelta(minutes=15)
DEFAULT_ENERGY_TIMEZONE: Final = "Asia/Amman"

# Follow-up refreshes after a command, in seconds from the command.
COMMAND_REFRESH_DELAYS: Final = (2, 5, 10)
# How long a sent command is shown as the expected state while waiting for
# the backend to report it.
COMMAND_PENDING_TIMEOUT: Final = 15

REQUEST_TIMEOUT: Final = 15
