# Phase 2 Code Templates - Quick Reference

**Purpose**: Copy-paste ready code templates for Phase 2 implementation
**Audience**: Developers implementing device discovery and switch platform
**Companion Document**: [PHASE2_IMPLEMENTATION_GUIDE.md](PHASE2_IMPLEMENTATION_GUIDE.md)

---

## Table of Contents

1. [Constants Definition](#constants-definition)
2. [Import Organization](#import-organization)
3. [Retry with Backoff](#retry-with-backoff)
4. [Rate Limiter](#rate-limiter)
5. [API Client Template](#api-client-template)
6. [Data Model Template](#data-model-template)
7. [Coordinator Template](#coordinator-template)
8. [Switch Entity Template](#switch-entity-template)
9. [Test Templates](#test-templates)

---

## Constants Definition

**File**: `custom_components/alexa/const.py` (additions)

```python
"""Constants for the Alexa integration (Phase 2 additions)."""

# API Endpoints
ALEXA_API_BASE_URL = "https://api.amazonalexa.com"
ENDPOINT_DEVICES = "/v1/devices"
ENDPOINT_DEVICE_STATE = "/v1/devices/{device_id}/state"
ENDPOINT_DIRECTIVE = "/v1/directives"

# Rate Limiting
RATE_LIMIT_CAPACITY = 20  # Max burst requests
RATE_LIMIT_REFILL_RATE = 10.0  # Requests per second

# Retry Configuration
MAX_RETRY_ATTEMPTS = 3
INITIAL_RETRY_DELAY = 1.0  # seconds
MAX_RETRY_DELAY = 60.0  # seconds
RETRY_BACKOFF_MULTIPLIER = 2.0

# Timeouts
REQUEST_TIMEOUT = 30  # seconds
UPDATE_TIMEOUT = 30  # seconds

# Update Intervals
DEFAULT_UPDATE_INTERVAL = 30  # seconds

# Alexa Capabilities (commonly used)
CAPABILITY_POWER = "Alexa.PowerController"
CAPABILITY_BRIGHTNESS = "Alexa.BrightnessController"
CAPABILITY_COLOR = "Alexa.ColorController"
CAPABILITY_COLOR_TEMPERATURE = "Alexa.ColorTemperatureController"
CAPABILITY_TEMPERATURE_SENSOR = "Alexa.TemperatureSensor"
CAPABILITY_LOCK = "Alexa.LockController"
CAPABILITY_THERMOSTAT = "Alexa.ThermostatController"
```

---

## Import Organization

**Standard Import Order** (follows PEP 8):

```python
"""Module docstring."""

from __future__ import annotations

# Standard library (alphabetical)
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

# Third-party libraries (alphabetical)
import aiohttp

# Home Assistant (grouped by category)
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

# Local imports (relative, alphabetical)
from .api_client import AlexaAPIClient
from .const import DOMAIN, CAPABILITY_POWER
from .coordinator import AlexaDeviceCoordinator
from .models import AlexaDevice, Capability

_LOGGER = logging.getLogger(__name__)
```

---

## Retry with Backoff

**Copy-paste ready retry decorator**:

```python
"""Retry logic with exponential backoff."""

import asyncio
import logging
from functools import wraps
from typing import TypeVar, Callable, Any

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    multiplier: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """Decorator to retry function with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        multiplier: Backoff multiplier
        exceptions: Exceptions to catch and retry

    Example:
        @retry_with_backoff(max_attempts=3)
        async def fetch_data():
            return await api.get()
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Exception | None = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)

                except exceptions as err:
                    last_exception = err

                    if attempt == max_attempts - 1:
                        # Last attempt - raise
                        _LOGGER.error(
                            "Max retries (%d) exceeded for %s: %s",
                            max_attempts,
                            func.__name__,
                            err,
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(initial_delay * (multiplier**attempt), max_delay)

                    _LOGGER.warning(
                        "%s failed (attempt %d/%d), retrying in %.1fs: %s",
                        func.__name__,
                        attempt + 1,
                        max_attempts,
                        delay,
                        err,
                    )

                    await asyncio.sleep(delay)

            # Should never reach here
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator


# Usage example:
@retry_with_backoff(max_attempts=3, initial_delay=1.0)
async def fetch_devices() -> list[dict[str, Any]]:
    """Fetch devices with automatic retry."""
    async with session.get(url) as response:
        response.raise_for_status()
        return await response.json()
```

---

## Rate Limiter

**Token bucket rate limiter** (copy-paste ready):

```python
"""Token bucket rate limiter."""

from datetime import datetime


class RateLimiter:
    """Token bucket rate limiter.

    Allows burst traffic up to capacity, then limits to refill_rate.

    Example:
        limiter = RateLimiter(capacity=20, refill_rate=10)

        # Acquire and wait if needed
        await limiter.acquire()
        await api.call()

        # Check if tokens available (non-blocking)
        if limiter.try_acquire():
            await api.call()
    """

    def __init__(
        self,
        capacity: int = 20,
        refill_rate: float = 10.0,
    ) -> None:
        """Initialize rate limiter.

        Args:
            capacity: Maximum tokens (burst size)
            refill_rate: Tokens per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_update = datetime.now()

    def _refill(self) -> None:
        """Refill tokens based on time elapsed."""
        now = datetime.now()
        elapsed = (now - self.last_update).total_seconds()

        # Add tokens based on elapsed time
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.refill_rate))
        self.last_update = now

    def try_acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens (non-blocking).

        Args:
            tokens: Number of tokens to acquire

        Returns:
            True if tokens acquired, False if rate limited
        """
        self._refill()

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True

        return False

    async def acquire(self, tokens: int = 1) -> None:
        """Acquire tokens, waiting if necessary (blocking).

        Args:
            tokens: Number of tokens to acquire
        """
        import asyncio

        while not self.try_acquire(tokens):
            # Calculate wait time
            needed = tokens - self.tokens
            wait_time = needed / self.refill_rate

            await asyncio.sleep(wait_time)
```

---

## API Client Template

**Minimal API client skeleton**:

```python
"""Alexa Smart Home API client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow

from .const import (
    ALEXA_API_BASE_URL,
    ENDPOINT_DEVICES,
    ENDPOINT_DIRECTIVE,
    REQUEST_TIMEOUT,
)
from .models import AlexaDevice

_LOGGER = logging.getLogger(__name__)


class AlexaAPIError(HomeAssistantError):
    """Base API error."""


class AlexaAuthenticationError(AlexaAPIError):
    """Authentication failed (401)."""


class AlexaRateLimitError(AlexaAPIError):
    """Rate limit exceeded (429)."""


class AlexaAPIClient:
    """Alexa API client."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: config_entry_oauth2_flow.OAuth2Session,
    ) -> None:
        """Initialize API client."""
        self.hass = hass
        self.session = session
        self.rate_limiter = RateLimiter()  # Add your RateLimiter

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make authenticated API request."""
        # Wait for rate limiter
        await self.rate_limiter.acquire()

        # Get access token
        await self.session.async_ensure_token_valid()
        token = self.session.token["access_token"]

        url = f"{ALEXA_API_BASE_URL}{endpoint}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

        # Make request with retry logic
        async with self.hass.helpers.aiohttp_client.async_get_clientsession().request(
            method,
            url,
            json=json_data,
            headers=headers,
            timeout=timeout,
        ) as response:
            if response.status == 401:
                raise AlexaAuthenticationError("Unauthorized")

            if response.status == 429:
                raise AlexaRateLimitError("Rate limited")

            response.raise_for_status()
            return await response.json()

    async def fetch_devices(self) -> list[AlexaDevice]:
        """Fetch all devices."""
        response = await self._request("GET", ENDPOINT_DEVICES)
        endpoints = response.get("endpoints", [])
        return [AlexaDevice.from_api_response(data) for data in endpoints]

    async def set_power_state(self, device_id: str, power_on: bool) -> None:
        """Set device power state."""
        directive = "TurnOn" if power_on else "TurnOff"
        payload = {
            "directive": {
                "header": {
                    "namespace": "Alexa.PowerController",
                    "name": directive,
                    "messageId": f"{device_id}-{asyncio.get_event_loop().time()}",
                    "payloadVersion": "3",
                },
                "endpoint": {"endpointId": device_id},
                "payload": {},
            }
        }
        await self._request("POST", ENDPOINT_DIRECTIVE, json_data=payload)
```

---

## Data Model Template

**Device and capability models**:

```python
"""Data models for Alexa devices."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Capability constants
CAPABILITY_POWER = "Alexa.PowerController"
CAPABILITY_BRIGHTNESS = "Alexa.BrightnessController"


@dataclass(frozen=True)
class Capability:
    """Alexa device capability."""

    type: str
    interface: str
    version: str = "3"
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Capability:
        """Create from API response."""
        return cls(
            type=data["type"],
            interface=data["interface"],
            version=data.get("version", "3"),
            properties=data.get("properties", {}),
        )


@dataclass
class AlexaDevice:
    """Alexa Smart Home device."""

    # Required
    id: str
    friendly_name: str
    capabilities: list[Capability]

    # Optional
    manufacturer: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    is_reachable: bool = True

    # State
    is_on: bool = False
    brightness: int | None = None

    @property
    def device_type(self) -> str:
        """Determine device type from capabilities."""
        if self.has_capability(CAPABILITY_BRIGHTNESS):
            return "light"
        if self.has_capability(CAPABILITY_POWER):
            return "switch"
        return "unknown"

    def has_capability(self, interface: str) -> bool:
        """Check if device has capability."""
        return any(cap.interface == interface for cap in self.capabilities)

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> AlexaDevice:
        """Create from API response."""
        return cls(
            id=data["endpointId"],
            friendly_name=data["friendlyName"],
            capabilities=[Capability.from_dict(c) for c in data.get("capabilities", [])],
            manufacturer=data.get("manufacturerName"),
            model=data.get("model"),
            firmware_version=data.get("firmwareVersion"),
            is_reachable=data.get("isReachable", True),
        )
```

---

## Coordinator Template

**DataUpdateCoordinator for Alexa devices**:

```python
"""Data update coordinator for Alexa devices."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import AlexaAPIClient, AlexaAuthenticationError, AlexaRateLimitError
from .const import DOMAIN, DEFAULT_UPDATE_INTERVAL
from .models import AlexaDevice

_LOGGER = logging.getLogger(__name__)


class AlexaDeviceCoordinator(DataUpdateCoordinator[dict[str, AlexaDevice]]):
    """Coordinator for Alexa device data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: AlexaAPIClient,
        update_interval: timedelta = timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_devices",
            update_interval=update_interval,
        )
        self.api_client = api_client

    async def _async_update_data(self) -> dict[str, AlexaDevice]:
        """Fetch data from API."""
        _LOGGER.debug("Updating Alexa device data")

        try:
            async with asyncio.timeout(30):
                devices = await self.api_client.fetch_devices()

            device_dict = {device.id: device for device in devices}

            _LOGGER.debug("Successfully updated %d devices", len(device_dict))
            return device_dict

        except AlexaAuthenticationError as err:
            _LOGGER.error("Authentication failed: %s", err)
            raise ConfigEntryAuthFailed("Authentication failed") from err

        except AlexaRateLimitError as err:
            _LOGGER.warning("Rate limited: %s", err)
            raise UpdateFailed(f"Rate limited: {err}") from err

        except asyncio.TimeoutError as err:
            _LOGGER.warning("Timeout fetching devices: %s", err)
            raise UpdateFailed("Timeout") from err

        except asyncio.CancelledError:
            _LOGGER.debug("Update cancelled")
            raise

        except Exception as err:
            _LOGGER.exception("Unexpected error: %s", err)
            raise UpdateFailed(f"Error: {err}") from err

    async def async_set_device_power(self, device_id: str, power_on: bool) -> None:
        """Set device power state."""
        await self.api_client.set_power_state(device_id, power_on)
        await self.async_request_refresh()
```

---

## Switch Entity Template

**Switch platform with CoordinatorEntity**:

```python
"""Support for Alexa switches."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CAPABILITY_POWER
from .coordinator import AlexaDeviceCoordinator
from .models import AlexaDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa switches."""
    coordinator: AlexaDeviceCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [
        AlexaSwitchEntity(coordinator, device_id)
        for device_id, device in coordinator.data.items()
        if device.has_capability(CAPABILITY_POWER)
    ]

    if entities:
        _LOGGER.info("Adding %d Alexa switches", len(entities))
        async_add_entities(entities)


class AlexaSwitchEntity(CoordinatorEntity[AlexaDeviceCoordinator], SwitchEntity):
    """Alexa switch entity."""

    def __init__(self, coordinator: AlexaDeviceCoordinator, device_id: str) -> None:
        """Initialize switch."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_switch_{device_id}"

    @property
    def device(self) -> AlexaDevice:
        """Get device from coordinator."""
        return self.coordinator.data[self._device_id]

    @property
    def name(self) -> str:
        """Return entity name."""
        return self.device.friendly_name

    @property
    def is_on(self) -> bool | None:
        """Return True if on."""
        return self.device.is_on

    @property
    def available(self) -> bool:
        """Return True if available."""
        return (
            self.coordinator.last_update_success
            and self._device_id in self.coordinator.data
            and self.device.is_reachable
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self.device.friendly_name,
            manufacturer=self.device.manufacturer or "Amazon",
            model=self.device.model,
            sw_version=self.device.firmware_version,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self.coordinator.async_set_device_power(self._device_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self.coordinator.async_set_device_power(self._device_id, False)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data."""
        self.async_write_ha_state()
```

---

## Test Templates

### Unit Test Template

```python
"""Test API client."""

import pytest
from unittest.mock import AsyncMock, patch
from aiohttp import ClientResponseError

from custom_components.alexa.api_client import (
    AlexaAPIClient,
    AlexaAuthenticationError,
)


@pytest.fixture
def mock_session():
    """Mock OAuth2Session."""
    session = AsyncMock()
    session.token = {"access_token": "test-token"}
    return session


@pytest.fixture
def api_client(hass, mock_session):
    """Create API client."""
    return AlexaAPIClient(hass, mock_session)


async def test_fetch_devices_success(api_client):
    """Test successful fetch."""
    mock_response = {
        "endpoints": [
            {
                "endpointId": "device-1",
                "friendlyName": "Test Device",
                "capabilities": [],
            }
        ]
    }

    with patch.object(api_client, "_request", return_value=mock_response):
        devices = await api_client.fetch_devices()

    assert len(devices) == 1
    assert devices[0].id == "device-1"


async def test_fetch_devices_auth_error(api_client):
    """Test auth error."""
    with patch.object(
        api_client,
        "_request",
        side_effect=AlexaAuthenticationError("Unauthorized"),
    ):
        with pytest.raises(AlexaAuthenticationError):
            await api_client.fetch_devices()
```

### Integration Test Template

```python
"""Test coordinator."""

import pytest
from datetime import timedelta
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.alexa.coordinator import AlexaDeviceCoordinator
from custom_components.alexa.api_client import AlexaAuthenticationError
from custom_components.alexa.models import AlexaDevice


async def test_coordinator_success(hass, mock_api_client):
    """Test successful update."""
    mock_api_client.fetch_devices.return_value = [
        AlexaDevice(id="device-1", friendly_name="Test", capabilities=[])
    ]

    coordinator = AlexaDeviceCoordinator(
        hass=hass,
        api_client=mock_api_client,
        update_interval=timedelta(seconds=30),
    )

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert "device-1" in coordinator.data


async def test_coordinator_auth_error(hass, mock_api_client):
    """Test auth error triggers reauth."""
    mock_api_client.fetch_devices.side_effect = AlexaAuthenticationError("Unauthorized")

    coordinator = AlexaDeviceCoordinator(hass=hass, api_client=mock_api_client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.async_refresh()
```

---

## Summary

### Quick Copy-Paste Checklist

- [ ] Constants: Add to `const.py`
- [ ] Imports: Follow standard order
- [ ] Rate Limiter: Copy `RateLimiter` class
- [ ] Retry: Copy `retry_with_backoff` decorator
- [ ] API Client: Use `AlexaAPIClient` template
- [ ] Models: Use `AlexaDevice` and `Capability` templates
- [ ] Coordinator: Use `AlexaDeviceCoordinator` template
- [ ] Switch: Use `AlexaSwitchEntity` template
- [ ] Tests: Use test templates for structure

### Key Patterns

1. **Always use type hints**: `-> list[AlexaDevice]`
2. **Always handle CancelledError**: Re-raise, don't catch
3. **Always use timeout**: `async with asyncio.timeout(30)`
4. **Always validate tokens**: `await session.async_ensure_token_valid()`
5. **Always log appropriately**: DEBUG/INFO/WARNING/ERROR/EXCEPTION
6. **Always use unique_id**: `f"{DOMAIN}_{entity_type}_{device_id}"`
7. **Always check available**: `coordinator.last_update_success and device.is_reachable`

---

**Related Documentation**:
- [Phase 2 Implementation Guide](PHASE2_IMPLEMENTATION_GUIDE.md) - Comprehensive guidance
- [Home Assistant Developer Docs](https://developers.home-assistant.io/) - Official HA docs
