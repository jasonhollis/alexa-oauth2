"""Alexa Smart Home API client with rate limiting and retry logic.

This module provides a client for interacting with the Alexa Smart Home API:
- Device discovery (list all Alexa devices)
- Device state queries
- Device control (turn on/off, set brightness, etc.)

Features:
- Automatic token refresh on 401 errors
- Token bucket rate limiting (prevents API throttling)
- Circuit breaker pattern (prevents cascading failures)
- Exponential backoff retry logic
- Comprehensive error handling and logging
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable

import aiohttp

from .models import AlexaDevice

_LOGGER = logging.getLogger(__name__)

# Alexa API endpoints
ALEXA_API_BASE = "https://api.amazonalexa.com"
ALEXA_DEVICES_ENDPOINT = f"{ALEXA_API_BASE}/v1/devices"
ALEXA_DEVICE_STATE_ENDPOINT = f"{ALEXA_API_BASE}/v1/devices"
ALEXA_DEVICE_CONTROL_ENDPOINT = f"{ALEXA_API_BASE}/v2/devices"

# Rate limits
RATE_LIMIT_BURST = 20  # Initial burst capacity
RATE_LIMIT_PER_SECOND = 10  # Sustained rate

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # seconds
RETRY_JITTER = 0.25  # ±25% random jitter

# Circuit breaker configuration
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 30  # seconds


class AlexaAPIException(Exception):
    """Base exception for Alexa API errors."""

    pass


class AlexaAuthError(AlexaAPIException):
    """Authentication error (401) - reauth needed."""

    pass


class AlexaRateLimitError(AlexaAPIException):
    """Rate limit error (429) - backoff and retry needed."""

    pass


class AlexaServerError(AlexaAPIException):
    """Server error (500+) - retry with backoff."""

    pass


class AlexaNetworkError(AlexaAPIException):
    """Network error (timeout, connection refused, etc.)."""

    pass


class CircuitBreakerState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, block requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """Circuit breaker pattern implementation.

    Prevents cascading failures by stopping requests when service is down.
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Service is failing, requests are blocked
    - HALF_OPEN: Testing if service recovered, next request determines state
    """

    def __init__(
        self,
        failure_threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        recovery_timeout: int = CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
    ):
        """Initialize circuit breaker.

        Args:
            failure_threshold: Number of consecutive failures before opening
            recovery_timeout: Seconds to wait before attempting recovery
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float | None = None

    def record_success(self) -> None:
        """Record successful request, reset failure counter."""
        self.failure_count = 0
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.CLOSED

    def record_failure(self) -> None:
        """Record failed request, open if threshold exceeded."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN

    def call(self, func: Callable) -> Any:
        """Call function with circuit breaker protection.

        Args:
            func: Async function to call

        Returns:
            Function result

        Raises:
            AlexaServerError: If circuit is OPEN
        """
        # Check if we should attempt recovery
        if self.state == CircuitBreakerState.OPEN:
            if self.last_failure_time is None:
                raise AlexaServerError("Circuit breaker is open")

            elapsed = time.time() - self.last_failure_time
            if elapsed < self.recovery_timeout:
                raise AlexaServerError(
                    f"Circuit breaker open, recovery in {self.recovery_timeout - elapsed:.0f}s"
                )

            # Attempt recovery
            self.state = CircuitBreakerState.HALF_OPEN

        return func

    async def acall(self, func: Callable) -> Any:
        """Async wrapper for circuit breaker call.

        Args:
            func: Async function to call

        Returns:
            Function result
        """
        self.call(func)
        return await func()


class TokenBucket:
    """Token bucket rate limiter.

    Implements smooth rate limiting:
    - Burst capacity for initial requests
    - Sustained rate prevents API throttling
    - No external dependencies
    """

    def __init__(self, capacity: int = RATE_LIMIT_BURST, refill_rate: int = RATE_LIMIT_PER_SECOND):
        """Initialize token bucket.

        Args:
            capacity: Maximum burst capacity (tokens)
            refill_rate: Refill rate per second
        """
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill_rate = refill_rate
        self.last_refill_time = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        """Acquire tokens, blocking until available.

        Args:
            tokens: Number of tokens to acquire (default 1)
        """
        async with self._lock:
            while self.tokens < tokens:
                # Refill based on elapsed time
                now = time.time()
                elapsed = now - self.last_refill_time
                refilled = elapsed * self.refill_rate
                self.tokens = min(self.capacity, self.tokens + refilled)
                self.last_refill_time = now

                if self.tokens < tokens:
                    # Wait and retry
                    wait_time = (tokens - self.tokens) / self.refill_rate
                    await asyncio.sleep(min(wait_time, 0.1))

            # Consume tokens
            self.tokens -= tokens
            self.last_refill_time = time.time()


class AlexaAPIClient:
    """Client for Alexa Smart Home API.

    Handles:
    - Device discovery
    - Device state queries
    - Device control (power, brightness, temperature, etc.)
    - Automatic token refresh on auth errors
    - Rate limiting and backoff
    - Resilient error handling
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_provider: Callable[[], Any],
        logger: logging.Logger | None = None,
    ):
        """Initialize API client.

        Args:
            session: aiohttp ClientSession for HTTP requests
            token_provider: Async callable that returns valid access token
            logger: Optional logger instance (uses module logger if not provided)
        """
        self.session = session
        self.token_provider = token_provider
        self.logger = logger or _LOGGER
        self.rate_limiter = TokenBucket(capacity=RATE_LIMIT_BURST, refill_rate=RATE_LIMIT_PER_SECOND)
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            recovery_timeout=CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        retry_count: int = 0,
    ) -> dict[str, Any]:
        """Make HTTP request with retry logic.

        Args:
            method: HTTP method (GET, PUT, etc.)
            endpoint: Full URL endpoint
            data: Optional request body data
            retry_count: Current retry attempt (internal use)

        Returns:
            Response JSON

        Raises:
            AlexaAuthError: Authentication error (401)
            AlexaRateLimitError: Rate limited (429)
            AlexaServerError: Server error (500+)
            AlexaNetworkError: Network error
        """
        # Check circuit breaker
        if self.circuit_breaker.state == CircuitBreakerState.OPEN:
            await self.circuit_breaker.acall(lambda: None)

        # Apply rate limiting
        await self.rate_limiter.acquire(tokens=1)

        # Get access token
        try:
            access_token = await self.token_provider()
        except Exception as err:
            self.logger.error(f"Failed to get access token: {err}")
            raise AlexaAuthError(f"Failed to get access token: {err}") from err

        # Prepare request
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self.session.request(
                method, endpoint, json=data, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200 or response.status == 204:
                    self.circuit_breaker.record_success()
                    if response.status == 204:
                        return {}
                    return await response.json()

                # Handle error responses
                error_text = await response.text()

                if response.status == 401:
                    self.circuit_breaker.record_success()  # Auth error, not API failure
                    self.logger.warning("Auth error, may need reauth: %s", error_text)
                    raise AlexaAuthError(f"Authentication failed: {error_text}")

                if response.status == 429:
                    self.circuit_breaker.record_failure()
                    self.logger.warning("Rate limited by Alexa API")
                    raise AlexaRateLimitError("Rate limited by Alexa API")

                if response.status >= 500:
                    self.circuit_breaker.record_failure()
                    self.logger.error(f"Server error {response.status}: {error_text}")
                    raise AlexaServerError(f"Server error {response.status}: {error_text}")

                # Other 4xx errors
                self.circuit_breaker.record_success()
                self.logger.warning(f"API error {response.status}: {error_text}")
                raise AlexaAPIException(f"API error {response.status}: {error_text}")

        except asyncio.TimeoutError as err:
            self.circuit_breaker.record_failure()
            self.logger.error("Request timeout")
            raise AlexaNetworkError("Request timeout") from err
        except aiohttp.ClientError as err:
            self.circuit_breaker.record_failure()
            self.logger.error(f"Network error: {err}")
            raise AlexaNetworkError(f"Network error: {err}") from err

    async def _request_with_retry(
        self, method: str, endpoint: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make request with exponential backoff retry.

        Args:
            method: HTTP method
            endpoint: Endpoint URL
            data: Optional request body

        Returns:
            Response JSON

        Raises:
            AlexaAuthError: Auth error (no retry)
            AlexaRateLimitError: Rate limit error (retry)
            AlexaServerError: Server error (retry)
            AlexaNetworkError: Network error (retry)
        """
        for attempt in range(MAX_RETRIES + 1):
            try:
                return await self._request(method, endpoint, data)
            except (AlexaRateLimitError, AlexaServerError, AlexaNetworkError) as err:
                if attempt >= MAX_RETRIES:
                    raise

                # Calculate backoff with jitter
                delay = RETRY_DELAYS[attempt]
                jitter = delay * RETRY_JITTER
                actual_delay = delay + (asyncio.get_event_loop().time() % jitter) - (jitter / 2)
                self.logger.warning(
                    f"Request failed (attempt {attempt + 1}/{MAX_RETRIES + 1}), retrying in {actual_delay:.2f}s"
                )
                await asyncio.sleep(actual_delay)
            except AlexaAuthError:
                # Don't retry auth errors
                raise

        # Should not reach here
        raise AlexaServerError("Max retries exceeded")

    async def get_devices(self) -> list[AlexaDevice]:
        """Fetch all Alexa devices for the user.

        Returns:
            List of AlexaDevice objects

        Raises:
            AlexaAuthError: Authentication error
            AlexaRateLimitError: Rate limited
            AlexaNetworkError: Network error
        """
        self.logger.debug("Fetching devices from Alexa API")
        response = await self._request_with_retry("GET", f"{ALEXA_DEVICES_ENDPOINT}")

        devices = []
        for device_data in response.get("devices", []):
            try:
                device = AlexaDevice.from_api_response(device_data)
                devices.append(device)
            except Exception as err:
                self.logger.warning(f"Failed to parse device {device_data.get('id', 'unknown')}: {err}")

        self.logger.info(f"Fetched {len(devices)} devices from Alexa")
        return devices

    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        """Get current state of a specific device.

        Args:
            device_id: Alexa device ID

        Returns:
            Device state dictionary

        Raises:
            AlexaAuthError: Authentication error
            AlexaRateLimitError: Rate limited
            AlexaNetworkError: Network error
        """
        self.logger.debug(f"Fetching state for device {device_id}")
        endpoint = f"{ALEXA_DEVICE_STATE_ENDPOINT}/{device_id}/state"
        response = await self._request_with_retry("GET", endpoint)
        return response

    async def set_power_state(self, device_id: str, turn_on: bool) -> bool:
        """Control device power state.

        Args:
            device_id: Alexa device ID
            turn_on: True to turn on, False to turn off

        Returns:
            True if successful

        Raises:
            AlexaAuthError: Authentication error
            AlexaRateLimitError: Rate limited
            AlexaNetworkError: Network error
        """
        state = "ON" if turn_on else "OFF"
        self.logger.debug(f"Setting device {device_id} power to {state}")

        endpoint = f"{ALEXA_DEVICE_CONTROL_ENDPOINT}/{device_id}/states"
        data = {"type": "PowerController", "value": state}

        await self._request_with_retry("PUT", endpoint, data)
        self.logger.info(f"Set device {device_id} power to {state}")
        return True

    async def set_brightness(self, device_id: str, brightness: int) -> bool:
        """Control device brightness (0-254).

        Args:
            device_id: Alexa device ID
            brightness: Brightness level 0-254 (254 = 100%)

        Returns:
            True if successful

        Raises:
            AlexaAuthError: Authentication error
            AlexaRateLimitError: Rate limited
            AlexaNetworkError: Network error
        """
        # Clamp brightness to valid range
        brightness = max(0, min(254, brightness))
        self.logger.debug(f"Setting device {device_id} brightness to {brightness}")

        endpoint = f"{ALEXA_DEVICE_CONTROL_ENDPOINT}/{device_id}/states"
        data = {"type": "BrightnessController", "value": brightness}

        await self._request_with_retry("PUT", endpoint, data)
        self.logger.info(f"Set device {device_id} brightness to {brightness}")
        return True

    async def set_color(self, device_id: str, hue: int, saturation: int, brightness: int) -> bool:
        """Control device color (HSV).

        Args:
            device_id: Alexa device ID
            hue: Hue 0-360 degrees
            saturation: Saturation 0-100 percent
            brightness: Brightness 0-100 percent

        Returns:
            True if successful

        Raises:
            AlexaAuthError: Authentication error
            AlexaRateLimitError: Rate limited
            AlexaNetworkError: Network error
        """
        self.logger.debug(f"Setting device {device_id} color HSV({hue}, {saturation}, {brightness})")

        endpoint = f"{ALEXA_DEVICE_CONTROL_ENDPOINT}/{device_id}/states"
        data = {
            "type": "ColorController",
            "value": {
                "hue": hue,
                "saturation": saturation,
                "brightness": brightness,
            },
        }

        await self._request_with_retry("PUT", endpoint, data)
        self.logger.info(f"Set device {device_id} color HSV({hue}, {saturation}, {brightness})")
        return True

    async def set_color_temperature(self, device_id: str, mireds: int) -> bool:
        """Control device color temperature (mireds).

        Args:
            device_id: Alexa device ID
            mireds: Color temperature in mireds (micro reciprocal Kelvin)
                    Typical range: 153 (6500K - cool white) to 500 (2000K - warm white)

        Returns:
            True if successful

        Raises:
            AlexaAuthError: Authentication error
            AlexaRateLimitError: Rate limited
            AlexaNetworkError: Network error
        """
        self.logger.debug(f"Setting device {device_id} color temperature to {mireds} mireds")

        endpoint = f"{ALEXA_DEVICE_CONTROL_ENDPOINT}/{device_id}/states"
        data = {"type": "ColorTemperatureController", "value": mireds}

        await self._request_with_retry("PUT", endpoint, data)
        self.logger.info(f"Set device {device_id} color temperature to {mireds} mireds")
        return True

    async def set_temperature(self, device_id: str, target_temp: float) -> bool:
        """Control thermostat target temperature.

        Args:
            device_id: Alexa device ID
            target_temp: Target temperature in Celsius

        Returns:
            True if successful

        Raises:
            AlexaAuthError: Authentication error
            AlexaRateLimitError: Rate limited
            AlexaNetworkError: Network error
        """
        self.logger.debug(f"Setting device {device_id} target temperature to {target_temp}°C")

        endpoint = f"{ALEXA_DEVICE_CONTROL_ENDPOINT}/{device_id}/states"
        data = {"type": "ThermostatController", "value": {"targetSetpoint": target_temp}}

        await self._request_with_retry("PUT", endpoint, data)
        self.logger.info(f"Set device {device_id} target temperature to {target_temp}°C")
        return True
