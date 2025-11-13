# Phase 2 Implementation Guide: Device Discovery & Switch Platform

**Purpose**: Comprehensive implementation guidance for building Alexa device discovery and control
**Target Audience**: Python developers implementing Home Assistant integrations
**Timeline**: 2-3 weeks (~800 lines of production code)
**Prerequisites**: Phase 1 OAuth2 implementation complete

---

## Table of Contents

1. [Overview](#overview)
2. [Technical Decisions Reference](#technical-decisions-reference)
3. [Implementation Checklist](#implementation-checklist)
4. [Module 1: API Client](#module-1-api-client)
5. [Module 2: Data Models](#module-2-data-models)
6. [Module 3: Coordinator](#module-3-coordinator)
7. [Module 4: Switch Platform](#module-4-switch-platform)
8. [Module 5: Integration Updates](#module-5-integration-updates)
9. [Error Handling Patterns](#error-handling-patterns)
10. [Testing Strategy](#testing-strategy)
11. [Common Pitfalls](#common-pitfalls)

---

## Overview

### What We're Building

```
┌─────────────────────────────────────────────────────────────┐
│                        Home Assistant                        │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  __init__.py (Entry)                  │  │
│  │  • Registers OAuth implementation                     │  │
│  │  • Creates coordinator                                │  │
│  │  • Forwards to platforms                              │  │
│  └───────────────┬───────────────────────────────────────┘  │
│                  │                                           │
│  ┌───────────────▼───────────────────────────────────────┐  │
│  │         coordinator.py (DataUpdateCoordinator)        │  │
│  │  • Polls Alexa API for device list                    │  │
│  │  • Manages refresh intervals                          │  │
│  │  • Provides data to entities                          │  │
│  └───────────────┬───────────────────────────────────────┘  │
│                  │                                           │
│        ┌─────────┴──────────┬──────────────┐               │
│        │                    │              │               │
│  ┌─────▼─────┐      ┌──────▼──────┐  ┌───▼────┐          │
│  │ switch.py │      │  light.py   │  │ etc... │          │
│  │           │      │             │  │        │          │
│  │ AlexaSwitch│     │ AlexaLight  │  │        │          │
│  │ Entity    │      │ Entity      │  │        │          │
│  └───────────┘      └─────────────┘  └────────┘          │
└─────────────────────────────────────────────────────────────┘
                       │
         ┌─────────────▼─────────────┐
         │   api_client.py           │
         │   • HTTP client           │
         │   • Retry logic           │
         │   • Rate limiting         │
         └─────────────┬─────────────┘
                       │
         ┌─────────────▼─────────────┐
         │   Alexa Smart Home API    │
         │   https://api.amazonalexa │
         │          .com/v1/         │
         └───────────────────────────┘
```

### Architecture Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Async Pattern | `async def` everywhere | HA is async-first, blocking kills performance |
| Coordinator | `DataUpdateCoordinator` | Built-in polling, caching, entity updates |
| Data Model | `@dataclass` | Simple, fast, perfect for DTOs |
| Retry Logic | Exponential backoff | Industry standard (AWS SDK, Google SDK) |
| Rate Limiting | Token bucket | Simple, effective, no external deps |
| Error Handling | Graceful degradation | Mark unavailable vs crash integration |
| Type Hints | Strict everywhere | Catch bugs at dev time, not runtime |

---

## Technical Decisions Reference

### 1. Async Patterns

**Rule**: Use `async def` for **all** I/O-bound operations

```python
# ✅ CORRECT: I/O operations are async
async def fetch_devices(self) -> list[AlexaDevice]:
    """Fetch devices from API (I/O bound)."""
    async with self.session.get(url) as response:
        return await response.json()

# ✅ CORRECT: Pure computation can be sync (but async is fine too)
def _parse_capability(self, raw: dict[str, Any]) -> Capability:
    """Parse capability data (CPU bound, instant)."""
    return Capability(
        type=raw["type"],
        interface=raw["interface"]
    )

# ❌ WRONG: Blocking call in async context
async def fetch_devices(self) -> list[AlexaDevice]:
    response = requests.get(url)  # BLOCKS EVENT LOOP!
    return response.json()
```

**When to use `asyncio.gather` vs sequential**:

```python
# ✅ Use gather for INDEPENDENT operations
async def update_all_devices(self) -> None:
    """Update all devices in parallel (3 API calls)."""
    devices = await self.coordinator.async_get_devices()

    # These calls are independent - run in parallel
    results = await asyncio.gather(
        self.api.get_device_state(devices[0].id),
        self.api.get_device_state(devices[1].id),
        self.api.get_device_state(devices[2].id),
        return_exceptions=True,  # Don't fail all if one fails
    )

# ✅ Use sequential for DEPENDENT operations
async def authenticate_and_fetch(self) -> list[AlexaDevice]:
    """Authenticate then fetch (second depends on first)."""
    token = await self.oauth.async_get_token()  # Must succeed first
    devices = await self.api.fetch_devices(token)  # Uses token
    return devices
```

**Cancellation and Cleanup**:

```python
class AlexaDeviceCoordinator(DataUpdateCoordinator):
    """Coordinator with proper cancellation."""

    def __init__(self, hass: HomeAssistant, ...) -> None:
        super().__init__(...)
        self._cleanup_task: asyncio.Task | None = None

    async def async_shutdown(self) -> None:
        """Clean shutdown with cancellation."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass  # Expected

    async def _async_update_data(self) -> dict[str, AlexaDevice]:
        """Update with cancellation support."""
        try:
            # Use timeout to prevent infinite hangs
            async with asyncio.timeout(30):
                return await self.api.fetch_devices()
        except asyncio.TimeoutError:
            raise UpdateFailed("Timeout fetching devices")
        except asyncio.CancelledError:
            _LOGGER.debug("Update cancelled")
            raise  # Re-raise to stop gracefully
```

### 2. Error Handling & Resilience

**Exponential Backoff Implementation** (copy-paste ready):

```python
from datetime import timedelta
import asyncio
from typing import TypeVar, Callable, Any

T = TypeVar("T")

class RetryConfig:
    """Retry configuration."""

    max_attempts: int = 3
    initial_delay: float = 1.0  # seconds
    max_delay: float = 60.0
    multiplier: float = 2.0
    jitter: bool = True  # Add randomness to prevent thundering herd

async def retry_with_backoff(
    func: Callable[..., T],
    *args: Any,
    config: RetryConfig | None = None,
    **kwargs: Any,
) -> T:
    """Retry function with exponential backoff.

    Example:
        result = await retry_with_backoff(
            api.get_devices,
            config=RetryConfig(max_attempts=5)
        )
    """
    config = config or RetryConfig()
    last_exception: Exception | None = None

    for attempt in range(config.max_attempts):
        try:
            return await func(*args, **kwargs)
        except Exception as err:
            last_exception = err

            if attempt == config.max_attempts - 1:
                # Last attempt - raise
                raise

            # Calculate delay with exponential backoff
            delay = min(
                config.initial_delay * (config.multiplier ** attempt),
                config.max_delay
            )

            # Add jitter (randomness) to prevent thundering herd
            if config.jitter:
                import random
                delay *= (0.5 + random.random())  # 50-150% of delay

            _LOGGER.warning(
                "Attempt %d/%d failed: %s. Retrying in %.1fs...",
                attempt + 1,
                config.max_attempts,
                err,
                delay,
            )

            await asyncio.sleep(delay)

    # Should never reach here, but satisfy type checker
    raise last_exception  # type: ignore[misc]
```

**Circuit Breaker Pattern** (for persistent failures):

```python
from enum import Enum
from datetime import datetime, timedelta

class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Failures detected, rejecting calls
    HALF_OPEN = "half_open"  # Testing if service recovered

class CircuitBreaker:
    """Circuit breaker for API calls.

    Pattern:
    - CLOSED: Normal operation, track failures
    - OPEN: Too many failures, reject calls immediately (fast fail)
    - HALF_OPEN: After timeout, allow one test call

    Usage:
        breaker = CircuitBreaker(failure_threshold=5, timeout=60)

        async def call_api():
            if not breaker.can_execute():
                raise CircuitBreakerOpen("API unavailable")

            try:
                result = await api.call()
                breaker.record_success()
                return result
            except Exception as err:
                breaker.record_failure()
                raise
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: int = 60,  # seconds before trying again
    ) -> None:
        self.failure_threshold = failure_threshold
        self.timeout = timedelta(seconds=timeout)

        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time: datetime | None = None

    def can_execute(self) -> bool:
        """Check if call should be executed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if timeout expired
            if (
                self.last_failure_time
                and datetime.now() - self.last_failure_time > self.timeout
            ):
                # Try half-open (allow one test call)
                self.state = CircuitState.HALF_OPEN
                return True
            return False  # Still open, reject

        # HALF_OPEN: Allow one call
        return True

    def record_success(self) -> None:
        """Record successful call."""
        self.failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record failed call."""
        self.failures += 1
        self.last_failure_time = datetime.now()

        if self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            _LOGGER.warning(
                "Circuit breaker opened after %d failures",
                self.failures
            )
```

**Graceful Degradation**:

```python
async def _async_update_data(self) -> dict[str, AlexaDevice]:
    """Update data with graceful degradation."""
    try:
        devices = await self.api.fetch_devices()
        return {d.id: d for d in devices}

    except AuthenticationError:
        # Auth failure - trigger reauth
        _LOGGER.error("Authentication failed, triggering reauth")
        raise ConfigEntryAuthFailed("Token invalid") from None

    except RateLimitError as err:
        # Rate limited - mark entities unavailable but don't fail
        _LOGGER.warning(
            "Rate limited by Alexa API, entities will be unavailable: %s",
            err
        )
        raise UpdateFailed(f"Rate limited: {err}") from err

    except asyncio.TimeoutError:
        # Timeout - temporary, retry next cycle
        _LOGGER.warning("Timeout fetching devices, will retry next cycle")
        raise UpdateFailed("Timeout") from None

    except Exception as err:
        # Unexpected error - log but don't crash
        _LOGGER.exception("Unexpected error fetching devices: %s", err)
        raise UpdateFailed(f"Unexpected error: {err}") from err
```

### 3. Rate Limiting

**Token Bucket Implementation** (simple, no external deps):

```python
from datetime import datetime
from typing import Final

class RateLimiter:
    """Token bucket rate limiter.

    Allows burst traffic up to capacity, then limits to refill_rate.

    Example:
        # Allow 10 requests per second, burst up to 20
        limiter = RateLimiter(capacity=20, refill_rate=10)

        if await limiter.acquire():
            await api.call()
        else:
            raise RateLimitError("Too many requests")
    """

    def __init__(
        self,
        capacity: int = 20,  # Max tokens (burst size)
        refill_rate: float = 10.0,  # Tokens per second
    ) -> None:
        self.capacity: Final = capacity
        self.refill_rate: Final = refill_rate

        self.tokens: float = float(capacity)
        self.last_update: datetime = datetime.now()

    def _refill(self) -> None:
        """Refill tokens based on time elapsed."""
        now = datetime.now()
        elapsed = (now - self.last_update).total_seconds()

        # Add tokens based on time elapsed
        self.tokens = min(
            self.capacity,
            self.tokens + (elapsed * self.refill_rate)
        )
        self.last_update = now

    async def acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens.

        Returns:
            True if tokens acquired, False if rate limited
        """
        self._refill()

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True

        return False

    async def acquire_wait(self, tokens: int = 1) -> None:
        """Acquire tokens, waiting if necessary."""
        while not await self.acquire(tokens):
            # Calculate wait time
            needed = tokens - self.tokens
            wait_time = needed / self.refill_rate

            _LOGGER.debug(
                "Rate limited, waiting %.2fs for %d tokens",
                wait_time,
                tokens
            )

            await asyncio.sleep(wait_time)
```

### 4. Home Assistant Patterns

**DataUpdateCoordinator Pattern**:

```python
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from datetime import timedelta

class AlexaDeviceCoordinator(DataUpdateCoordinator[dict[str, AlexaDevice]]):
    """Coordinator for Alexa devices.

    Responsibilities:
    - Poll API at regular intervals
    - Cache device data
    - Notify entities when data changes
    - Handle errors gracefully

    Type Parameter:
    - dict[str, AlexaDevice]: Maps device_id → AlexaDevice
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: AlexaAPIClient,
        update_interval: timedelta = timedelta(seconds=30),
    ) -> None:
        """Initialize coordinator.

        Args:
            hass: Home Assistant instance
            api_client: API client for fetching devices
            update_interval: How often to poll (default 30s)
        """
        super().__init__(
            hass,
            _LOGGER,
            name="Alexa Devices",  # Shows in logs
            update_interval=update_interval,
        )
        self.api_client = api_client

    async def _async_update_data(self) -> dict[str, AlexaDevice]:
        """Fetch data from API.

        Called automatically by coordinator at update_interval.

        Returns:
            Dictionary mapping device_id to AlexaDevice

        Raises:
            UpdateFailed: Temporary failure (will retry)
            ConfigEntryAuthFailed: Auth failed (trigger reauth)
        """
        try:
            # Fetch devices with timeout
            async with asyncio.timeout(30):
                devices = await self.api_client.fetch_devices()

            # Convert list to dict for fast lookups
            return {device.id: device for device in devices}

        except AuthenticationError:
            raise ConfigEntryAuthFailed("Authentication failed") from None
        except Exception as err:
            raise UpdateFailed(f"Error fetching devices: {err}") from err
```

**CoordinatorEntity Pattern**:

```python
from homeassistant.helpers.update_coordinator import CoordinatorEntity

class AlexaSwitchEntity(CoordinatorEntity[AlexaDeviceCoordinator], SwitchEntity):
    """Alexa switch entity.

    Inherits from:
    - CoordinatorEntity: Gets automatic updates from coordinator
    - SwitchEntity: Provides switch-specific capabilities
    """

    def __init__(
        self,
        coordinator: AlexaDeviceCoordinator,
        device_id: str,
    ) -> None:
        """Initialize switch entity.

        Args:
            coordinator: Data coordinator
            device_id: Alexa device ID
        """
        # Initialize coordinator entity
        super().__init__(coordinator)

        self._device_id = device_id
        self._attr_unique_id = f"alexa_switch_{device_id}"

    @property
    def device(self) -> AlexaDevice:
        """Get device from coordinator data."""
        return self.coordinator.data[self._device_id]

    @property
    def is_on(self) -> bool | None:
        """Return if switch is on."""
        return self.device.is_on

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Entity is available if:
        # 1. Coordinator has data (not None)
        # 2. Device is in coordinator data
        # 3. Device is reachable
        return (
            self.coordinator.last_update_success
            and self._device_id in self.coordinator.data
            and self.device.is_reachable
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn switch on."""
        await self.coordinator.api_client.set_power_state(
            self._device_id,
            True
        )
        # Request immediate coordinator refresh
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn switch off."""
        await self.coordinator.api_client.set_power_state(
            self._device_id,
            False
        )
        # Request immediate coordinator refresh
        await self.coordinator.async_request_refresh()
```

**Device Registry Pattern**:

```python
from homeassistant.helpers.device_registry import DeviceInfo

class AlexaSwitchEntity(CoordinatorEntity[AlexaDeviceCoordinator], SwitchEntity):
    """Switch with device registry integration."""

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for device registry.

        This creates/updates an entry in the device registry.
        Multiple entities can share the same device.
        """
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self.device.friendly_name,
            manufacturer=self.device.manufacturer or "Amazon",
            model=self.device.model,
            sw_version=self.device.firmware_version,
            # Link to Alexa app (if available)
            configuration_url=f"https://alexa.amazon.com/spa/index.html#appliances/device/{self._device_id}",
        )
```

**hass.data Structure**:

```python
# In __init__.py async_setup_entry:
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alexa from config entry."""

    # Get OAuth session from Phase 1
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    # Create API client
    api_client = AlexaAPIClient(
        session=session,
        hass=hass,
    )

    # Create coordinator
    coordinator = AlexaDeviceCoordinator(
        hass=hass,
        api_client=api_client,
        update_interval=timedelta(seconds=30),
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store in hass.data for platforms to access
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "api_client": api_client,
        "session": session,
    }

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(
        entry,
        [Platform.SWITCH, Platform.LIGHT]
    )

    return True

# In switch.py async_setup_entry:
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa switches."""

    # Get coordinator from hass.data
    coordinator: AlexaDeviceCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Create switch entities for all switch devices
    entities = [
        AlexaSwitchEntity(coordinator, device_id)
        for device_id, device in coordinator.data.items()
        if device.has_capability("Alexa.PowerController")
    ]

    async_add_entities(entities)
```

### 5. Data Models

**Use `@dataclass` for DTOs** (simple, fast, built-in):

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)  # Immutable (hashable)
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
    """Alexa device model."""

    # Required fields
    id: str
    friendly_name: str
    capabilities: list[Capability]

    # Optional fields with defaults
    manufacturer: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    is_reachable: bool = True
    is_on: bool = False

    # Computed properties
    @property
    def device_type(self) -> str:
        """Get primary device type."""
        # Logic to determine type from capabilities
        if self.has_capability("Alexa.PowerController"):
            return "switch"
        elif self.has_capability("Alexa.BrightnessController"):
            return "light"
        return "unknown"

    def has_capability(self, interface: str) -> bool:
        """Check if device has capability."""
        return any(cap.interface == interface for cap in self.capabilities)

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> AlexaDevice:
        """Create device from API response.

        Example API response:
        {
            "endpointId": "device-123",
            "friendlyName": "Living Room Light",
            "capabilities": [
                {
                    "type": "AlexaInterface",
                    "interface": "Alexa.PowerController",
                    "version": "3"
                }
            ],
            "manufacturerName": "Philips",
            "model": "Hue Bulb",
            "isReachable": true
        }
        """
        return cls(
            id=data["endpointId"],
            friendly_name=data["friendlyName"],
            capabilities=[
                Capability.from_dict(cap)
                for cap in data.get("capabilities", [])
            ],
            manufacturer=data.get("manufacturerName"),
            model=data.get("model"),
            firmware_version=data.get("firmwareVersion"),
            is_reachable=data.get("isReachable", True),
        )
```

**Why NOT Pydantic**:
- Adds external dependency (HA prefers stdlib)
- Overkill for simple DTOs
- Slower than dataclasses
- `@dataclass` is sufficient for 99% of cases

**Why NOT TypedDict**:
- No validation
- No defaults
- No methods
- Harder to evolve

### 6. Logging Strategy

**What to Log at Each Level**:

```python
# DEBUG: Development/troubleshooting details
_LOGGER.debug(
    "Fetched %d devices from API in %.2fs",
    len(devices),
    elapsed_time
)

# INFO: Normal operational events
_LOGGER.info(
    "Alexa integration configured for user %s with %d devices",
    user_name,
    device_count
)

# WARNING: Recoverable errors, degraded state
_LOGGER.warning(
    "Rate limited by Alexa API, retry in %ds: %s",
    retry_delay,
    error_message
)

# ERROR: Errors that prevent operation
_LOGGER.error(
    "Failed to fetch devices after %d retries: %s",
    max_retries,
    error_message
)

# EXCEPTION: Unexpected errors (includes stack trace)
try:
    result = await api.call()
except Exception:
    _LOGGER.exception("Unexpected error calling API")
```

**Sensitive Data Handling**:

```python
# ❌ WRONG: Logs access token
_LOGGER.debug("Using token: %s", access_token)

# ✅ CORRECT: Log partial token for debugging
_LOGGER.debug("Using token: %s...", access_token[:8])

# ✅ CORRECT: Log token length/type only
_LOGGER.debug(
    "Using token type=%s length=%d",
    token_type,
    len(access_token)
)
```

---

## Implementation Checklist

Implement modules in this order (dependencies flow down):

### Week 1: Foundation (Models + API Client)

- [ ] **Day 1: Models (`models.py`)**
  - [ ] Create `Capability` dataclass
  - [ ] Create `AlexaDevice` dataclass
  - [ ] Add validation methods
  - [ ] Add `from_api_response` factory methods
  - [ ] Write unit tests (test data parsing)

- [ ] **Day 2-3: API Client (`api_client.py`)**
  - [ ] Create `AlexaAPIClient` class
  - [ ] Implement `fetch_devices()` method
  - [ ] Add retry logic with exponential backoff
  - [ ] Add rate limiting (token bucket)
  - [ ] Add error handling (401, 429, timeout)
  - [ ] Write unit tests (mock API responses)

- [ ] **Day 4: Testing & Polish**
  - [ ] Integration tests (real API calls)
  - [ ] Error scenario tests
  - [ ] Performance tests (rate limits work?)
  - [ ] Code review and refactoring

### Week 2: Coordinator + Platform

- [ ] **Day 5-6: Coordinator (`coordinator.py`)**
  - [ ] Create `AlexaDeviceCoordinator` class
  - [ ] Implement `_async_update_data()` method
  - [ ] Add error handling (auth, rate limit, timeout)
  - [ ] Test coordinator polling
  - [ ] Test error recovery

- [ ] **Day 7-8: Switch Platform (`switch.py`)**
  - [ ] Create `AlexaSwitchEntity` class
  - [ ] Implement `is_on` property
  - [ ] Implement `async_turn_on()` method
  - [ ] Implement `async_turn_off()` method
  - [ ] Add device info
  - [ ] Write entity tests

- [ ] **Day 9: Integration (`__init__.py` updates)**
  - [ ] Create coordinator in `async_setup_entry`
  - [ ] Store coordinator in `hass.data`
  - [ ] Forward to switch platform
  - [ ] Test end-to-end flow

### Week 3: Polish + Testing

- [ ] **Day 10-11: Integration Testing**
  - [ ] Test with real Alexa devices
  - [ ] Test rate limiting behavior
  - [ ] Test error scenarios (network down, auth fail)
  - [ ] Test entity availability
  - [ ] Test device registry entries

- [ ] **Day 12-13: Documentation + Code Review**
  - [ ] Document API client usage
  - [ ] Document coordinator setup
  - [ ] Document adding new platforms
  - [ ] Code review and refactoring
  - [ ] Performance profiling

- [ ] **Day 14: Release Preparation**
  - [ ] Update CHANGELOG
  - [ ] Update README
  - [ ] Create release notes
  - [ ] Tag release

---

## Module 1: API Client

### File: `custom_components/alexa/api_client.py`

**Purpose**: HTTP client for Alexa Smart Home API with retry, rate limiting, and error handling

**Responsibilities**:
- Make authenticated HTTP requests to Alexa API
- Retry failed requests with exponential backoff
- Rate limit requests to avoid 429 errors
- Parse API responses into domain models
- Handle API-specific errors (401, 429, 500, timeout)

**Dependencies**:
- OAuth2Session (from Phase 1) for token management
- aiohttp for async HTTP
- models.py for data structures

**Size**: ~250 lines

### Implementation

```python
"""Alexa Smart Home API client.

This module provides an HTTP client for the Alexa Smart Home API with:
- Automatic token refresh via OAuth2Session
- Retry logic with exponential backoff
- Rate limiting to prevent 429 errors
- Comprehensive error handling
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Final

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN
from .models import AlexaDevice

_LOGGER = logging.getLogger(__name__)

# API Endpoints
ALEXA_API_BASE_URL: Final = "https://api.amazonalexa.com"
ENDPOINT_DEVICES: Final = "/v1/devices"
ENDPOINT_DEVICE_STATE: Final = "/v1/devices/{device_id}/state"
ENDPOINT_DIRECTIVE: Final = "/v1/directives"

# Rate Limiting
RATE_LIMIT_CAPACITY: Final = 20  # Max burst requests
RATE_LIMIT_REFILL_RATE: Final = 10.0  # Requests per second

# Retry Configuration
MAX_RETRY_ATTEMPTS: Final = 3
INITIAL_RETRY_DELAY: Final = 1.0  # seconds
MAX_RETRY_DELAY: Final = 60.0  # seconds
RETRY_BACKOFF_MULTIPLIER: Final = 2.0

# Timeouts
REQUEST_TIMEOUT: Final = 30  # seconds


class AlexaAPIError(HomeAssistantError):
    """Base class for Alexa API errors."""


class AlexaAuthenticationError(AlexaAPIError):
    """Authentication failed (401)."""


class AlexaRateLimitError(AlexaAPIError):
    """Rate limit exceeded (429)."""


class AlexaAPITimeout(AlexaAPIError):
    """Request timeout."""


class RateLimiter:
    """Token bucket rate limiter."""

    def __init__(
        self,
        capacity: int = RATE_LIMIT_CAPACITY,
        refill_rate: float = RATE_LIMIT_REFILL_RATE,
    ) -> None:
        """Initialize rate limiter."""
        self.capacity: Final = capacity
        self.refill_rate: Final = refill_rate
        self.tokens: float = float(capacity)
        self.last_update: datetime = datetime.now()

    def _refill(self) -> None:
        """Refill tokens based on time elapsed."""
        now = datetime.now()
        elapsed = (now - self.last_update).total_seconds()
        self.tokens = min(
            self.capacity, self.tokens + (elapsed * self.refill_rate)
        )
        self.last_update = now

    async def acquire(self, tokens: int = 1) -> None:
        """Acquire tokens, waiting if necessary."""
        while True:
            self._refill()

            if self.tokens >= tokens:
                self.tokens -= tokens
                return

            # Calculate wait time
            needed = tokens - self.tokens
            wait_time = needed / self.refill_rate

            _LOGGER.debug("Rate limited, waiting %.2fs", wait_time)
            await asyncio.sleep(wait_time)


class AlexaAPIClient:
    """Client for Alexa Smart Home API.

    Example:
        client = AlexaAPIClient(hass, session)
        devices = await client.fetch_devices()
        await client.set_power_state("device-123", True)
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: config_entry_oauth2_flow.OAuth2Session,
    ) -> None:
        """Initialize API client.

        Args:
            hass: Home Assistant instance
            session: OAuth2 session for token management
        """
        self.hass = hass
        self.session = session
        self.rate_limiter = RateLimiter()

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make authenticated API request with retry.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            json_data: JSON body (optional)
            params: Query parameters (optional)

        Returns:
            API response as dictionary

        Raises:
            AlexaAuthenticationError: Authentication failed
            AlexaRateLimitError: Rate limit exceeded
            AlexaAPITimeout: Request timeout
            AlexaAPIError: Other API errors
        """
        # Wait for rate limiter
        await self.rate_limiter.acquire()

        # Get access token (automatically refreshes if expired)
        try:
            await self.session.async_ensure_token_valid()
        except Exception as err:
            raise AlexaAuthenticationError(
                f"Failed to get valid token: {err}"
            ) from err

        token_data = self.session.token
        access_token = token_data["access_token"]

        url = f"{ALEXA_API_BASE_URL}{endpoint}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # Retry logic with exponential backoff
        last_exception: Exception | None = None
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                async with self.hass.helpers.aiohttp_client.async_get_clientsession().request(
                    method,
                    url,
                    json=json_data,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                ) as response:
                    # Handle HTTP errors
                    if response.status == 401:
                        raise AlexaAuthenticationError("Unauthorized (401)")

                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After", "60")
                        raise AlexaRateLimitError(
                            f"Rate limited (429), retry after {retry_after}s"
                        )

                    if response.status >= 500:
                        raise AlexaAPIError(
                            f"Server error ({response.status})"
                        )

                    response.raise_for_status()

                    # Parse JSON response
                    return await response.json()

            except asyncio.TimeoutError as err:
                last_exception = err
                if attempt == MAX_RETRY_ATTEMPTS - 1:
                    raise AlexaAPITimeout("Request timeout") from err

            except (AlexaAuthenticationError, AlexaRateLimitError):
                # Don't retry auth or rate limit errors
                raise

            except Exception as err:
                last_exception = err
                if attempt == MAX_RETRY_ATTEMPTS - 1:
                    raise AlexaAPIError(f"API request failed: {err}") from err

            # Calculate exponential backoff delay
            delay = min(
                INITIAL_RETRY_DELAY * (RETRY_BACKOFF_MULTIPLIER**attempt),
                MAX_RETRY_DELAY,
            )

            _LOGGER.warning(
                "Request failed (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                MAX_RETRY_ATTEMPTS,
                delay,
                last_exception,
            )

            await asyncio.sleep(delay)

        # Should never reach here
        raise AlexaAPIError("Max retries exceeded") from last_exception

    async def fetch_devices(self) -> list[AlexaDevice]:
        """Fetch all devices from Alexa API.

        Returns:
            List of AlexaDevice objects

        Raises:
            AlexaAuthenticationError: Authentication failed
            AlexaAPIError: API request failed
        """
        _LOGGER.debug("Fetching devices from Alexa API")

        try:
            response = await self._request("GET", ENDPOINT_DEVICES)

            # Parse devices from response
            endpoints = response.get("endpoints", [])
            devices = [
                AlexaDevice.from_api_response(device_data)
                for device_data in endpoints
            ]

            _LOGGER.info("Fetched %d devices from Alexa API", len(devices))
            return devices

        except Exception as err:
            _LOGGER.error("Failed to fetch devices: %s", err)
            raise

    async def set_power_state(self, device_id: str, power_on: bool) -> None:
        """Turn device on or off.

        Args:
            device_id: Alexa device ID
            power_on: True to turn on, False to turn off

        Raises:
            AlexaAuthenticationError: Authentication failed
            AlexaAPIError: API request failed
        """
        directive = "TurnOn" if power_on else "TurnOff"

        _LOGGER.debug(
            "Setting power state for device %s: %s",
            device_id,
            directive,
        )

        payload = {
            "directive": {
                "header": {
                    "namespace": "Alexa.PowerController",
                    "name": directive,
                    "messageId": f"{device_id}-{datetime.now().timestamp()}",
                    "payloadVersion": "3",
                },
                "endpoint": {
                    "endpointId": device_id,
                },
                "payload": {},
            }
        }

        try:
            await self._request("POST", ENDPOINT_DIRECTIVE, json_data=payload)
            _LOGGER.debug("Successfully set power state for device %s", device_id)

        except Exception as err:
            _LOGGER.error(
                "Failed to set power state for device %s: %s",
                device_id,
                err,
            )
            raise

    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        """Get current state of device.

        Args:
            device_id: Alexa device ID

        Returns:
            Device state dictionary

        Raises:
            AlexaAuthenticationError: Authentication failed
            AlexaAPIError: API request failed
        """
        endpoint = ENDPOINT_DEVICE_STATE.format(device_id=device_id)
        return await self._request("GET", endpoint)
```

### Key Design Decisions

1. **Rate Limiting**: Token bucket allows burst traffic (20 requests) then limits to 10/s
2. **Retry Logic**: 3 attempts with exponential backoff (1s, 2s, 4s)
3. **Error Handling**: Separate exception types for different errors (auth, rate limit, timeout)
4. **Token Management**: OAuth2Session handles token refresh automatically
5. **Logging**: DEBUG for operations, INFO for results, WARNING/ERROR for failures

---

## Module 2: Data Models

### File: `custom_components/alexa/models.py`

**Purpose**: Domain models for Alexa devices and capabilities

**Responsibilities**:
- Represent Alexa devices as Python objects
- Parse API responses into models
- Provide type-safe access to device properties
- Determine device type from capabilities

**Dependencies**: None (pure data structures)

**Size**: ~150 lines

### Implementation

```python
"""Data models for Alexa devices and capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Alexa capability interfaces (commonly used)
CAPABILITY_POWER = "Alexa.PowerController"
CAPABILITY_BRIGHTNESS = "Alexa.BrightnessController"
CAPABILITY_COLOR = "Alexa.ColorController"
CAPABILITY_COLOR_TEMPERATURE = "Alexa.ColorTemperatureController"
CAPABILITY_TEMPERATURE_SENSOR = "Alexa.TemperatureSensor"
CAPABILITY_LOCK = "Alexa.LockController"
CAPABILITY_THERMOSTAT = "Alexa.ThermostatController"


@dataclass(frozen=True)
class Capability:
    """Alexa device capability.

    Represents an interface that a device supports (e.g., PowerController).
    """

    type: str  # Usually "AlexaInterface"
    interface: str  # e.g., "Alexa.PowerController"
    version: str = "3"
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Capability:
        """Create capability from API response.

        Args:
            data: Capability dictionary from API

        Returns:
            Capability object

        Example:
            {
                "type": "AlexaInterface",
                "interface": "Alexa.PowerController",
                "version": "3",
                "properties": {
                    "supported": [{"name": "powerState"}],
                    "retrievable": true
                }
            }
        """
        return cls(
            type=data["type"],
            interface=data["interface"],
            version=data.get("version", "3"),
            properties=data.get("properties", {}),
        )


@dataclass
class AlexaDevice:
    """Alexa Smart Home device.

    Represents a device discovered from the Alexa API.
    """

    # Required fields
    id: str  # Alexa endpoint ID (unique)
    friendly_name: str  # User-visible name
    capabilities: list[Capability]  # What the device can do

    # Optional fields
    manufacturer: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    is_reachable: bool = True

    # State (populated separately)
    is_on: bool = False
    brightness: int | None = None  # 0-100
    color_temp: int | None = None  # Kelvin
    temperature: float | None = None  # Celsius

    @property
    def device_type(self) -> str:
        """Determine device type from capabilities.

        Returns:
            Device type string: "switch", "light", "lock", etc.
        """
        # Check capabilities in priority order
        if self.has_capability(CAPABILITY_BRIGHTNESS):
            return "light"
        if self.has_capability(CAPABILITY_LOCK):
            return "lock"
        if self.has_capability(CAPABILITY_THERMOSTAT):
            return "climate"
        if self.has_capability(CAPABILITY_POWER):
            return "switch"
        if self.has_capability(CAPABILITY_TEMPERATURE_SENSOR):
            return "sensor"

        return "unknown"

    def has_capability(self, interface: str) -> bool:
        """Check if device supports capability.

        Args:
            interface: Capability interface (e.g., "Alexa.PowerController")

        Returns:
            True if device has capability
        """
        return any(cap.interface == interface for cap in self.capabilities)

    def get_capability(self, interface: str) -> Capability | None:
        """Get capability by interface name.

        Args:
            interface: Capability interface (e.g., "Alexa.PowerController")

        Returns:
            Capability object or None if not found
        """
        for cap in self.capabilities:
            if cap.interface == interface:
                return cap
        return None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> AlexaDevice:
        """Create device from Alexa API response.

        Args:
            data: Device dictionary from API

        Returns:
            AlexaDevice object

        Example API response:
            {
                "endpointId": "device-123",
                "friendlyName": "Living Room Light",
                "manufacturerName": "Philips",
                "model": "Hue Bulb",
                "firmwareVersion": "1.2.3",
                "isReachable": true,
                "capabilities": [
                    {
                        "type": "AlexaInterface",
                        "interface": "Alexa.PowerController",
                        "version": "3"
                    },
                    {
                        "type": "AlexaInterface",
                        "interface": "Alexa.BrightnessController",
                        "version": "3"
                    }
                ]
            }
        """
        return cls(
            id=data["endpointId"],
            friendly_name=data["friendlyName"],
            capabilities=[
                Capability.from_dict(cap) for cap in data.get("capabilities", [])
            ],
            manufacturer=data.get("manufacturerName"),
            model=data.get("model"),
            firmware_version=data.get("firmwareVersion"),
            is_reachable=data.get("isReachable", True),
        )
```

### Key Design Decisions

1. **`@dataclass`**: Simple, fast, type-safe (no need for Pydantic)
2. **`frozen=True` for Capability**: Immutable (hashable, thread-safe)
3. **Factory Methods**: `from_api_response()` centralizes parsing logic
4. **Type Hints**: Strict typing throughout (catches bugs early)
5. **Capability Check**: `has_capability()` provides clean API for checking support

---

## Module 3: Coordinator

### File: `custom_components/alexa/coordinator.py`

**Purpose**: DataUpdateCoordinator for polling Alexa devices

**Responsibilities**:
- Poll Alexa API at regular intervals (default 30s)
- Cache device data for entities
- Notify entities when data changes
- Handle errors gracefully (mark entities unavailable)

**Dependencies**:
- api_client.py for API calls
- models.py for data structures
- Home Assistant's DataUpdateCoordinator

**Size**: ~150 lines

### Implementation

```python
"""Data update coordinator for Alexa devices."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api_client import (
    AlexaAPIClient,
    AlexaAuthenticationError,
    AlexaRateLimitError,
)
from .const import DOMAIN
from .models import AlexaDevice

_LOGGER = logging.getLogger(__name__)

# Update interval (how often to poll Alexa API)
DEFAULT_UPDATE_INTERVAL = timedelta(seconds=30)


class AlexaDeviceCoordinator(DataUpdateCoordinator[dict[str, AlexaDevice]]):
    """Coordinator for Alexa device data.

    Responsibilities:
    - Poll Alexa API at regular intervals
    - Cache device data for entities
    - Notify entities when data changes
    - Handle errors gracefully

    Data Structure:
    - dict[str, AlexaDevice]: Maps device_id → AlexaDevice
    - Entities access via: coordinator.data[device_id]
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: AlexaAPIClient,
        update_interval: timedelta = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        """Initialize coordinator.

        Args:
            hass: Home Assistant instance
            api_client: API client for fetching devices
            update_interval: How often to poll (default 30s)
        """
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_devices",
            update_interval=update_interval,
        )
        self.api_client = api_client

    async def _async_update_data(self) -> dict[str, AlexaDevice]:
        """Fetch device data from Alexa API.

        Called automatically by coordinator at update_interval.

        Returns:
            Dictionary mapping device_id to AlexaDevice

        Raises:
            UpdateFailed: Temporary failure (entities marked unavailable, will retry)
            ConfigEntryAuthFailed: Auth failed (triggers reauth flow)
        """
        _LOGGER.debug("Updating Alexa device data")

        try:
            # Fetch devices with timeout (prevent infinite hangs)
            async with asyncio.timeout(30):
                devices = await self.api_client.fetch_devices()

            # Convert list to dict for fast entity lookups
            device_dict = {device.id: device for device in devices}

            _LOGGER.debug(
                "Successfully updated %d devices",
                len(device_dict),
            )

            return device_dict

        except AlexaAuthenticationError as err:
            # Auth failed - trigger reauth flow
            _LOGGER.error(
                "Authentication failed, triggering reauth: %s",
                err,
            )
            raise ConfigEntryAuthFailed("Authentication failed") from err

        except AlexaRateLimitError as err:
            # Rate limited - temporary failure, retry next cycle
            _LOGGER.warning(
                "Rate limited by Alexa API, entities will be unavailable: %s",
                err,
            )
            raise UpdateFailed(f"Rate limited: {err}") from err

        except asyncio.TimeoutError as err:
            # Timeout - temporary failure, retry next cycle
            _LOGGER.warning(
                "Timeout fetching devices, will retry next cycle: %s",
                err,
            )
            raise UpdateFailed("Timeout fetching devices") from err

        except asyncio.CancelledError:
            # Coordinator cancelled (HA shutting down)
            _LOGGER.debug("Update cancelled")
            raise

        except Exception as err:
            # Unexpected error - log and mark entities unavailable
            _LOGGER.exception(
                "Unexpected error fetching devices: %s",
                err,
            )
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def async_set_device_power(
        self,
        device_id: str,
        power_on: bool,
    ) -> None:
        """Set device power state.

        Args:
            device_id: Alexa device ID
            power_on: True to turn on, False to turn off

        Raises:
            HomeAssistantError: Command failed
        """
        await self.api_client.set_power_state(device_id, power_on)

        # Request immediate refresh to update state
        await self.async_request_refresh()
```

### Key Design Decisions

1. **Inheritance**: Extends `DataUpdateCoordinator[dict[str, AlexaDevice]]`
   - Type parameter ensures type safety
   - `coordinator.data` is `dict[str, AlexaDevice]`

2. **Update Interval**: 30 seconds default (configurable)
   - Balance between freshness and API rate limits
   - Can be adjusted based on user needs

3. **Error Handling**:
   - `ConfigEntryAuthFailed`: Triggers reauth (401 errors)
   - `UpdateFailed`: Temporary failure, entities unavailable (429, timeout)
   - Entities automatically marked unavailable when update fails

4. **Data Structure**: `dict[str, AlexaDevice]` for O(1) lookups
   - Entities do: `coordinator.data[device_id]`
   - Fast, efficient, type-safe

5. **Timeout**: 30 second timeout prevents infinite hangs
   - Coordinator will retry next cycle

---

## Module 4: Switch Platform

### File: `custom_components/alexa/switch.py`

**Purpose**: Switch platform for Alexa power-controllable devices

**Responsibilities**:
- Create switch entities for devices with PowerController capability
- Provide turn_on/turn_off functionality
- Report device state (on/off, available)
- Integrate with Home Assistant device registry

**Dependencies**:
- coordinator.py for device data
- models.py for device representation
- Home Assistant's SwitchEntity

**Size**: ~150 lines

### Implementation

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

from .const import DOMAIN
from .coordinator import AlexaDeviceCoordinator
from .models import CAPABILITY_POWER, AlexaDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa switches from config entry.

    Args:
        hass: Home Assistant instance
        entry: Config entry
        async_add_entities: Callback to add entities
    """
    # Get coordinator from hass.data
    coordinator: AlexaDeviceCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    # Create switch entities for devices with PowerController capability
    entities: list[AlexaSwitchEntity] = []
    for device_id, device in coordinator.data.items():
        if device.has_capability(CAPABILITY_POWER):
            entities.append(
                AlexaSwitchEntity(
                    coordinator=coordinator,
                    device_id=device_id,
                )
            )

    if entities:
        _LOGGER.info("Adding %d Alexa switch entities", len(entities))
        async_add_entities(entities)


class AlexaSwitchEntity(
    CoordinatorEntity[AlexaDeviceCoordinator], SwitchEntity
):
    """Alexa switch entity.

    Represents a power-controllable device from Alexa.
    """

    def __init__(
        self,
        coordinator: AlexaDeviceCoordinator,
        device_id: str,
    ) -> None:
        """Initialize switch entity.

        Args:
            coordinator: Data coordinator
            device_id: Alexa device ID
        """
        super().__init__(coordinator)

        self._device_id = device_id

        # Set unique_id (must be stable across restarts)
        self._attr_unique_id = f"{DOMAIN}_switch_{device_id}"

        # Set entity ID (can be customized by user)
        # Format: switch.alexa_living_room_light
        device_name = self.device.friendly_name.lower().replace(" ", "_")
        self.entity_id = f"switch.{DOMAIN}_{device_name}"

    @property
    def device(self) -> AlexaDevice:
        """Get device from coordinator data."""
        return self.coordinator.data[self._device_id]

    @property
    def name(self) -> str:
        """Return entity name."""
        return self.device.friendly_name

    @property
    def is_on(self) -> bool | None:
        """Return True if switch is on."""
        return self.device.is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Entity is available if:
        1. Coordinator has successfully fetched data
        2. Device exists in coordinator data
        3. Device is reachable (reported by Alexa)
        """
        return (
            self.coordinator.last_update_success
            and self._device_id in self.coordinator.data
            and self.device.is_reachable
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for device registry.

        Creates/updates device in Home Assistant device registry.
        Multiple entities (switch, sensor, etc.) can share same device.
        """
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self.device.friendly_name,
            manufacturer=self.device.manufacturer or "Amazon",
            model=self.device.model,
            sw_version=self.device.firmware_version,
            configuration_url=(
                f"https://alexa.amazon.com/spa/index.html#appliances"
                f"/device/{self._device_id}"
            ),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn switch on.

        Args:
            **kwargs: Additional arguments (unused)
        """
        _LOGGER.debug("Turning on switch %s", self._device_id)

        await self.coordinator.async_set_device_power(
            self._device_id,
            power_on=True,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn switch off.

        Args:
            **kwargs: Additional arguments (unused)
        """
        _LOGGER.debug("Turning off switch %s", self._device_id)

        await self.coordinator.async_set_device_power(
            self._device_id,
            power_on=False,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator.

        Called automatically when coordinator.data changes.
        Updates entity state in Home Assistant.
        """
        self.async_write_ha_state()
```

### Key Design Decisions

1. **CoordinatorEntity**: Inherits from `CoordinatorEntity[AlexaDeviceCoordinator]`
   - Automatic updates when coordinator data changes
   - Type parameter ensures type safety
   - No manual subscription needed

2. **unique_id**: Stable identifier (`alexa_switch_{device_id}`)
   - Never changes (persists across restarts)
   - Allows user customization of entity_id and name

3. **entity_id**: Auto-generated from friendly name
   - User can customize after entity creation
   - Format: `switch.alexa_living_room_light`

4. **available Property**: Multi-condition check
   - Coordinator success + device in data + device reachable
   - HA automatically shows "unavailable" badge when False

5. **Immediate Refresh**: `async_request_refresh()` after state change
   - Updates entity state immediately (not after 30s poll)
   - Provides responsive UX

6. **Device Registry**: `device_info` property
   - Creates device in HA device registry
   - Multiple entities can share same device
   - Shows device details in UI

---

## Module 5: Integration Updates

### File: `custom_components/alexa/__init__.py` (updates)

**Purpose**: Update entry point to create coordinator and forward to platforms

**Changes**:
1. Import coordinator and API client
2. Create coordinator in `async_setup_entry`
3. Store coordinator in `hass.data`
4. Add switch platform to `PLATFORMS`
5. Forward to platforms

### Implementation Changes

```python
"""The Amazon Alexa integration (Phase 2 updates)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .api_client import AlexaAPIClient
from .const import DOMAIN
from .coordinator import AlexaDeviceCoordinator
from .oauth import AlexaOAuth2Implementation

_LOGGER = logging.getLogger(__name__)

# Platforms supported by this integration
# Phase 2: Add switch platform
PLATFORMS: list[Platform] = [Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Amazon Alexa from a config entry (Phase 2 version)."""
    _LOGGER.info(
        "Setting up Alexa integration for user %s (entry_id=%s)",
        entry.data.get("name", "Unknown"),
        entry.entry_id,
    )

    # Initialize integration storage
    hass.data.setdefault(DOMAIN, {})

    # Register OAuth implementation if not already registered
    # (Phase 1 code - unchanged)
    current_implementations = (
        await config_entry_oauth2_flow.async_get_implementations(hass, DOMAIN)
    )
    if DOMAIN not in current_implementations:
        _LOGGER.debug("Registering AlexaOAuth2Implementation")
        client_id = entry.data.get("client_id")
        client_secret = entry.data.get("client_secret")
        if not client_id or not client_secret:
            _LOGGER.error("Missing client_id or client_secret")
            return False
        config_entry_oauth2_flow.async_register_implementation(
            hass,
            DOMAIN,
            AlexaOAuth2Implementation(hass, DOMAIN, client_id, client_secret),
        )

    # Get OAuth implementation and create session
    # (Phase 1 code - unchanged)
    try:
        implementation = (
            await config_entry_oauth2_flow.async_get_config_entry_implementation(
                hass, entry
            )
        )
    except ValueError as err:
        _LOGGER.error("Failed to get OAuth implementation: %s", err)
        return False

    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    # Validate token
    try:
        await session.async_ensure_token_valid()
    except Exception as err:
        _LOGGER.error("Failed to validate token: %s", err)
        # Continue anyway - framework will trigger reauth if needed

    # ============== PHASE 2 ADDITIONS ==============

    # Create API client
    api_client = AlexaAPIClient(hass=hass, session=session)

    # Create device coordinator
    coordinator = AlexaDeviceCoordinator(
        hass=hass,
        api_client=api_client,
        update_interval=timedelta(seconds=30),
    )

    # Fetch initial data (raises ConfigEntryNotReady if fails)
    await coordinator.async_config_entry_first_refresh()

    # Store session, API client, and coordinator in hass.data
    hass.data[DOMAIN][entry.entry_id] = {
        "session": session,
        "implementation": implementation,
        "api_client": api_client,  # NEW
        "coordinator": coordinator,  # NEW
        "user_id": entry.data.get("user_id"),
        "name": entry.data.get("name"),
        "email": entry.data.get("email"),
    }

    # Forward entry setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "Alexa integration configured with %d devices",
        len(coordinator.data),
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload config entry (Phase 2 version)."""
    _LOGGER.info("Unloading Alexa integration (entry_id=%s)", entry.entry_id)

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if not unload_ok:
        _LOGGER.warning("Failed to unload platforms for entry %s", entry.entry_id)
        return False

    # Clean up hass.data
    if entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)

    _LOGGER.info("Alexa integration unload complete")
    return True
```

### Key Changes

1. **Imports**: Added `api_client`, `coordinator`, `timedelta`, `Platform.SWITCH`
2. **API Client**: Created in `async_setup_entry`
3. **Coordinator**: Created and stored in `hass.data`
4. **First Refresh**: `async_config_entry_first_refresh()` fetches initial data
5. **Platform Forward**: Now forwards to switch platform
6. **Logging**: Added device count to success log

---

## Error Handling Patterns

### HTTP Error Mapping

| HTTP Status | Exception | Action |
|-------------|-----------|--------|
| 401 | `AlexaAuthenticationError` | Raise `ConfigEntryAuthFailed` (trigger reauth) |
| 429 | `AlexaRateLimitError` | Raise `UpdateFailed` (retry next cycle) |
| 500-599 | `AlexaAPIError` | Raise `UpdateFailed` (retry next cycle) |
| Timeout | `AlexaAPITimeout` | Raise `UpdateFailed` (retry next cycle) |

### Error Recovery Flow

```python
async def _async_update_data(self) -> dict[str, AlexaDevice]:
    """Update with comprehensive error handling."""
    try:
        return await self._fetch_devices()

    except AlexaAuthenticationError:
        # Auth failed - user action required
        raise ConfigEntryAuthFailed("Token invalid")

    except AlexaRateLimitError as err:
        # Rate limited - temporary, retry automatically
        raise UpdateFailed(f"Rate limited: {err}")

    except asyncio.TimeoutError:
        # Timeout - temporary, retry automatically
        raise UpdateFailed("Timeout")

    except Exception as err:
        # Unexpected - log with stack trace
        _LOGGER.exception("Unexpected error: %s", err)
        raise UpdateFailed(f"Error: {err}")
```

### State Transitions

```
┌─────────────┐
│   Initial   │  First refresh fetches data
└──────┬──────┘
       │ Success
       ▼
┌─────────────┐
│   Running   │  Coordinator polls every 30s
└──────┬──────┘
       │
       ├─ Success ──> Update entities, continue
       │
       ├─ UpdateFailed ──> Mark entities unavailable, retry next cycle
       │
       └─ ConfigEntryAuthFailed ──> Trigger reauth flow
                                      (Persistent notification to user)
```

---

## Testing Strategy

### Unit Tests (Fast, No I/O)

```python
"""Test API client (unit tests)."""

import pytest
from unittest.mock import AsyncMock, patch
from aiohttp import ClientResponseError

from custom_components.alexa.api_client import (
    AlexaAPIClient,
    AlexaAuthenticationError,
    AlexaRateLimitError,
)


@pytest.fixture
def mock_session():
    """Mock OAuth2Session."""
    session = AsyncMock()
    session.token = {"access_token": "test-token"}
    return session


@pytest.fixture
def api_client(hass, mock_session):
    """Create API client with mocked session."""
    return AlexaAPIClient(hass, mock_session)


async def test_fetch_devices_success(api_client):
    """Test successful device fetch."""
    mock_response = {
        "endpoints": [
            {
                "endpointId": "device-1",
                "friendlyName": "Test Device",
                "capabilities": [
                    {
                        "type": "AlexaInterface",
                        "interface": "Alexa.PowerController",
                        "version": "3",
                    }
                ],
            }
        ]
    }

    with patch.object(api_client, "_request", return_value=mock_response):
        devices = await api_client.fetch_devices()

    assert len(devices) == 1
    assert devices[0].id == "device-1"
    assert devices[0].friendly_name == "Test Device"


async def test_fetch_devices_auth_error(api_client):
    """Test authentication error handling."""
    with patch.object(
        api_client,
        "_request",
        side_effect=AlexaAuthenticationError("Unauthorized"),
    ):
        with pytest.raises(AlexaAuthenticationError):
            await api_client.fetch_devices()


async def test_retry_logic(api_client):
    """Test retry with exponential backoff."""
    call_count = 0

    async def mock_request(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise AlexaAPIError("Temporary error")
        return {"endpoints": []}

    with patch.object(api_client, "_request", side_effect=mock_request):
        devices = await api_client.fetch_devices()

    assert call_count == 3  # Failed twice, succeeded on third attempt
    assert len(devices) == 0
```

### Integration Tests (With HA)

```python
"""Test coordinator (integration tests)."""

import pytest
from datetime import timedelta
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.alexa.coordinator import AlexaDeviceCoordinator
from custom_components.alexa.api_client import AlexaAuthenticationError


async def test_coordinator_update_success(hass, mock_api_client):
    """Test successful coordinator update."""
    # Mock API client to return devices
    mock_api_client.fetch_devices.return_value = [
        AlexaDevice(
            id="device-1",
            friendly_name="Test Device",
            capabilities=[],
        )
    ]

    coordinator = AlexaDeviceCoordinator(
        hass=hass,
        api_client=mock_api_client,
        update_interval=timedelta(seconds=30),
    )

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert "device-1" in coordinator.data
    assert coordinator.data["device-1"].friendly_name == "Test Device"


async def test_coordinator_auth_error(hass, mock_api_client):
    """Test coordinator auth error triggers reauth."""
    mock_api_client.fetch_devices.side_effect = AlexaAuthenticationError(
        "Token expired"
    )

    coordinator = AlexaDeviceCoordinator(
        hass=hass,
        api_client=mock_api_client,
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.async_refresh()


async def test_coordinator_temporary_error(hass, mock_api_client):
    """Test coordinator handles temporary errors."""
    mock_api_client.fetch_devices.side_effect = [
        AlexaRateLimitError("Rate limited"),  # First call fails
        [AlexaDevice(id="device-1", ...)],  # Second call succeeds
    ]

    coordinator = AlexaDeviceCoordinator(hass=hass, api_client=mock_api_client)

    # First update fails
    with pytest.raises(UpdateFailed):
        await coordinator.async_refresh()

    # Second update succeeds
    await coordinator.async_refresh()
    assert coordinator.last_update_success
```

### Entity Tests

```python
"""Test switch entity."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.alexa.switch import AlexaSwitchEntity
from custom_components.alexa.models import AlexaDevice, Capability


async def test_switch_state(hass: HomeAssistant, coordinator):
    """Test switch state reflects device state."""
    device = AlexaDevice(
        id="switch-1",
        friendly_name="Test Switch",
        capabilities=[Capability(type="AlexaInterface", interface="Alexa.PowerController")],
        is_on=True,
    )

    coordinator.data = {"switch-1": device}

    entity = AlexaSwitchEntity(coordinator, "switch-1")

    assert entity.is_on is True
    assert entity.name == "Test Switch"
    assert entity.available is True


async def test_switch_turn_on(hass: HomeAssistant, coordinator, mock_api_client):
    """Test turning switch on."""
    entity = AlexaSwitchEntity(coordinator, "switch-1")

    await entity.async_turn_on()

    mock_api_client.set_power_state.assert_called_once_with("switch-1", True)
    coordinator.async_request_refresh.assert_called_once()


async def test_switch_unavailable_when_unreachable(hass: HomeAssistant, coordinator):
    """Test switch marked unavailable when device unreachable."""
    device = AlexaDevice(
        id="switch-1",
        friendly_name="Test Switch",
        capabilities=[],
        is_reachable=False,  # Device offline
    )

    coordinator.data = {"switch-1": device}
    entity = AlexaSwitchEntity(coordinator, "switch-1")

    assert entity.available is False
```

---

## Common Pitfalls

### 1. Blocking Calls in Async Context

```python
# ❌ WRONG: Blocks event loop
async def fetch_devices(self):
    response = requests.get(url)  # BLOCKS!
    return response.json()

# ✅ CORRECT: Uses async HTTP
async def fetch_devices(self):
    async with self.session.get(url) as response:
        return await response.json()
```

### 2. Not Handling Cancellation

```python
# ❌ WRONG: Ignores CancelledError
async def _async_update_data(self):
    try:
        return await self.api.fetch()
    except Exception:
        raise UpdateFailed("Error")

# ✅ CORRECT: Re-raises CancelledError
async def _async_update_data(self):
    try:
        return await self.api.fetch()
    except asyncio.CancelledError:
        raise  # Don't catch cancellation
    except Exception as err:
        raise UpdateFailed(f"Error: {err}")
```

### 3. Forgetting Timeout

```python
# ❌ WRONG: Can hang forever
async def fetch(self):
    return await self.api.get_devices()

# ✅ CORRECT: Has timeout
async def fetch(self):
    async with asyncio.timeout(30):
        return await self.api.get_devices()
```

### 4. Mutable Default Arguments

```python
# ❌ WRONG: Mutable default is shared
@dataclass
class Device:
    capabilities: list[Capability] = []  # SHARED!

# ✅ CORRECT: Use field(default_factory)
@dataclass
class Device:
    capabilities: list[Capability] = field(default_factory=list)
```

### 5. Not Using unique_id

```python
# ❌ WRONG: No unique_id
class MyEntity(Entity):
    def __init__(self, name):
        self.name = name  # Name can change!

# ✅ CORRECT: Stable unique_id
class MyEntity(Entity):
    def __init__(self, device_id):
        self._attr_unique_id = f"alexa_{device_id}"
```

### 6. Forgetting Type Hints

```python
# ❌ WRONG: No type hints
async def fetch_devices(self):
    return await self.api.get()

# ✅ CORRECT: Full type hints
async def fetch_devices(self) -> list[AlexaDevice]:
    return await self.api.get()
```

### 7. Not Re-raising Auth Errors

```python
# ❌ WRONG: Swallows auth error
async def _async_update_data(self):
    try:
        return await self.fetch()
    except Exception:
        return {}  # Hides auth failure!

# ✅ CORRECT: Propagates auth error
async def _async_update_data(self):
    try:
        return await self.fetch()
    except AuthError:
        raise ConfigEntryAuthFailed()
```

### 8. Inefficient Data Structures

```python
# ❌ WRONG: List requires O(n) lookup
async def _async_update_data(self) -> list[AlexaDevice]:
    return await self.api.fetch_devices()

# Entity must search list
device = next(d for d in coordinator.data if d.id == self.device_id)

# ✅ CORRECT: Dict provides O(1) lookup
async def _async_update_data(self) -> dict[str, AlexaDevice]:
    devices = await self.api.fetch_devices()
    return {d.id: d for d in devices}

# Entity has instant lookup
device = coordinator.data[self.device_id]
```

---

## Summary

### Implementation Order

1. **Week 1**: Models + API Client (foundation)
2. **Week 2**: Coordinator + Switch Platform (core functionality)
3. **Week 3**: Testing + Polish (production-ready)

### Key Patterns

- **Async everywhere**: `async def` for all I/O
- **Retry with backoff**: 3 attempts, exponential (1s, 2s, 4s)
- **Rate limiting**: Token bucket (20 burst, 10/s sustained)
- **Graceful degradation**: Mark unavailable, don't crash
- **Type hints**: Strict typing throughout
- **DataUpdateCoordinator**: Built-in polling and caching
- **CoordinatorEntity**: Automatic entity updates

### Success Criteria

- [ ] All devices discovered and displayed
- [ ] Turn on/off works reliably
- [ ] Entities marked unavailable on errors
- [ ] Reauth triggered on 401
- [ ] Rate limits respected (no 429 errors)
- [ ] 90%+ test coverage
- [ ] No blocking calls in async code
- [ ] Type hints pass strict mypy

---

**Next Steps**: Start with `models.py`, then `api_client.py`, test thoroughly before moving to coordinator.
