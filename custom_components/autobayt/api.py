"""Small async client for the Autobayt cloud API.

Nothing in this module logs request parameters, tokens, account or device
identifiers. Errors carry only the HTTP status and a short reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT

LOGIN_BAD_CREDENTIALS = {111, 112}


class AutobaytError(Exception):
    """Base error for the Autobayt API."""


class AutobaytConnectionError(AutobaytError):
    """The backend could not be reached or answered with a server error."""


class AutobaytAuthError(AutobaytError):
    """The token or the credentials were rejected."""


class AutobaytRateLimitError(AutobaytError):
    """The backend asked us to slow down."""

    def __init__(self, retry_after: float | None = None) -> None:
        """Store the server's Retry-After hint, if any."""
        super().__init__("rate limited")
        self.retry_after = retry_after


class AutobaytResponseError(AutobaytError):
    """The backend answered with something unexpected."""


class AutobaytClient:
    """Talks to the Autobayt backend over HTTPS."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        token: str | None = None,
    ) -> None:
        """Create a client bound to one backend origin."""
        self._session = session
        self._host = host.rstrip("/")
        self._token = token

    @property
    def token(self) -> str | None:
        """Return the current login token."""
        return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        login: bool = False,
    ) -> Any:
        headers = {"Accept": "application/json"}
        # The login token goes with every request.
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with self._session.request(
                method,
                f"{self._host}{path}",
                params=params,
                json=json,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    body = None
                if status in (401, 403):
                    raise AutobaytAuthError(f"HTTP {status}")
                if status == 429:
                    raise AutobaytRateLimitError(_retry_after(resp.headers))
                if status >= 500:
                    raise AutobaytConnectionError(f"HTTP {status}")
                if (
                    login
                    and status == 400
                    and isinstance(body, dict)
                    and body.get("error_code") in LOGIN_BAD_CREDENTIALS
                ):
                    raise AutobaytAuthError("invalid credentials")
                if status >= 400:
                    raise AutobaytResponseError(f"HTTP {status}")
                return body
        except TimeoutError as err:
            raise AutobaytConnectionError("timeout") from err
        except aiohttp.ClientError as err:
            raise AutobaytConnectionError(type(err).__name__) from err

    async def login(self, email: str, password: str) -> tuple[str, str]:
        """Sign in and return (token, user_id). The token is kept on the client."""
        body = await self._request(
            "POST",
            "/v1/user/login",
            json={"email": email.strip().lower(), "password": password, "source": "email"},
            login=True,
        )
        if not isinstance(body, dict):
            raise AutobaytResponseError("login: unexpected body")
        token = body.get("token")
        user_id = (body.get("user_info") or {}).get("_id")
        if not token or not user_id:
            raise AutobaytResponseError("login: missing token or user")
        self._token = token
        return token, str(user_id)

    async def get_locations(self, user_id: str) -> list[dict[str, Any]]:
        """Return the account's locations (homes)."""
        body = await self._request("GET", "/v1/user/locations", params={"user_id": user_id})
        if not isinstance(body, list):
            raise AutobaytResponseError("locations: expected a list")
        return [loc for loc in body if isinstance(loc, dict) and loc.get("_id")]

    async def get_things(self, user_id: str, location_id: str) -> list[dict[str, Any]]:
        """Return the devices of one location.

        The backend may return devices from other locations of the same
        account, so the result is filtered by location here.
        """
        body = await self._request(
            "GET",
            "/v1/user/get-things",
            params={"user_id": user_id, "loc_id": location_id},
        )
        if not isinstance(body, list):
            raise AutobaytResponseError("get-things: expected a list")
        return [
            dev
            for dev in body
            if isinstance(dev, dict)
            and dev.get("device_id")
            and str(dev.get("loc_id")) == location_id
        ]

    async def get_device(self, device_id: str) -> dict[str, Any]:
        """Return one device."""
        body = await self._request("GET", "/v1/device", params={"device_id": device_id})
        if not isinstance(body, dict):
            raise AutobaytResponseError("device: expected an object")
        return body

    async def set_buttons(self, device_id: str, button_ids: list[int], states: list[bool]) -> None:
        """Ask the backend to switch relay channels.

        A 200 only means the request was accepted, not that the relay moved;
        the coordinator confirms the state from later polls.
        """
        if len(button_ids) != len(states) or not button_ids:
            raise ValueError("button_ids and states must have the same length")
        await self._request(
            "POST",
            "/v1/device/trigger",
            json={
                "device_id": device_id,
                "btnIds": [int(b) for b in button_ids],
                "btnStates": [bool(s) for s in states],
            },
        )

    async def get_energy_live(self, location_id: str) -> dict[str, Any]:
        """Return current power for the location and its devices."""
        body = await self._request("GET", "/v1/user/energy/live", params={"loc_id": location_id})
        if not isinstance(body, dict):
            raise AutobaytResponseError("energy live: expected an object")
        return body

    async def get_energy_period(
        self, location_id: str, period: str, date: str, timezone: str
    ) -> dict[str, Any]:
        """Return consumption for a day, week or month."""
        body = await self._request(
            "GET",
            "/v1/user/energy",
            params={
                "loc_id": location_id,
                "period": period,
                "date": date,
                "timezone": timezone,
            },
        )
        if not isinstance(body, dict):
            raise AutobaytResponseError("energy: expected an object")
        return body


def _retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
