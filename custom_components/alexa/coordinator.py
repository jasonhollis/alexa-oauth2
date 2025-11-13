"""Device discovery and state update coordinator for Alexa integration.

This module provides the AlexaDeviceCoordinator which manages:
- Periodic device discovery (lists all Alexa devices)
- State updates (power state, brightness, temperature, etc.)
- Error handling and resilience
- Integration with Home Assistant's DataUpdateCoordinator

The coordinator uses a two-tiered polling strategy:
- Full device discovery every 15 minutes (detects new/removed devices)
- State updates every 5 minutes (power state, brightness, etc.)
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import (
    AlexaAPIClient,
    AlexaAuthError,
    AlexaNetworkError,
    AlexaRateLimitError,
    AlexaServerError,
)
from .models import AlexaDevice

_LOGGER = logging.getLogger(__name__)

# Update intervals
UPDATE_INTERVAL = timedelta(minutes=5)  # State update interval
DEVICE_DISCOVERY_INTERVAL = 900  # Device discovery interval (15 minutes) in seconds


class AlexaDeviceCoordinator(DataUpdateCoordinator[dict[str, AlexaDevice]]):
    """Coordinator for Alexa device discovery and state management.

    Manages periodic polling of Alexa devices with:
    - Full device discovery every 15 minutes
    - State updates every 5 minutes
    - Graceful error handling and retry logic
    - Integration with Home Assistant's DataUpdateCoordinator
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: AlexaAPIClient,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize device coordinator.

        Args:
            hass: Home Assistant instance
            api_client: AlexaAPIClient for API calls
            logger: Optional logger instance
        """
        super().__init__(
            hass,
            logger or _LOGGER,
            name="Alexa Device Coordinator",
            update_interval=UPDATE_INTERVAL,
        )
        self.api_client = api_client
        self._last_device_discovery = 0.0

    async def _async_update_data(self) -> dict[str, AlexaDevice]:
        """Update device data from Alexa API.

        Called by DataUpdateCoordinator periodically (every 5 minutes by default).
        Implements two-tiered polling:
        - Full discovery every 15 minutes (detects new/removed devices)
        - State-only updates every 5 minutes (faster, less API load)

        Returns:
            Dictionary mapping device_id to AlexaDevice

        Raises:
            UpdateFailed: On API errors (triggers DataUpdateCoordinator retry)
            ConfigEntryAuthFailed: On auth errors (triggers reauth flow)
        """
        current_time = time.time()
        time_since_discovery = current_time - self._last_device_discovery
        should_discover = time_since_discovery >= DEVICE_DISCOVERY_INTERVAL

        try:
            if should_discover:
                # Full device discovery
                self.logger.debug("Running full device discovery")
                devices = await self.api_client.get_devices()
                self._last_device_discovery = current_time

                # Convert list to dictionary keyed by device_id
                device_dict = {device.id: device for device in devices}
                self.logger.info(f"Discovered {len(device_dict)} devices")
                return device_dict
            else:
                # State-only update (faster, uses cached device list)
                self.logger.debug("Updating device states")
                if not self.data:
                    # No cached data, force discovery
                    self.logger.debug("No cached devices, forcing discovery")
                    devices = await self.api_client.get_devices()
                    self._last_device_discovery = current_time
                    device_dict = {device.id: device for device in devices}
                    self.logger.info(f"Discovered {len(device_dict)} devices")
                    return device_dict

                # Update state for existing devices
                updated_count = 0
                for device_id, device in self.data.items():
                    try:
                        state = await self.api_client.get_device_state(device_id)
                        device.update_state(state)
                        updated_count += 1
                    except Exception as err:
                        # Log but continue with other devices
                        self.logger.warning(f"Failed to update state for device {device_id}: {err}")

                self.logger.debug(f"Updated states for {updated_count} devices")
                return self.data

        except AlexaAuthError as err:
            self.logger.error(f"Authentication error: {err}")
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except (AlexaRateLimitError, AlexaServerError, AlexaNetworkError) as err:
            self.logger.warning(f"API error during update: {err}")
            raise UpdateFailed(f"Error updating devices: {err}") from err
        except Exception as err:
            self.logger.exception(f"Unexpected error during update: {err}")
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def async_refresh_devices(self) -> None:
        """Force immediate device discovery.

        Resets discovery timer and triggers update immediately.
        Useful for user-triggered refresh or after device setup.
        """
        self.logger.info("Forcing device discovery refresh")
        self._last_device_discovery = 0.0
        await self.async_refresh()

    @property
    def devices(self) -> dict[str, AlexaDevice]:
        """Get all discovered devices.

        Returns:
            Dictionary mapping device_id to AlexaDevice
        """
        return self.data or {}

    @property
    def available_devices(self) -> list[AlexaDevice]:
        """Get list of devices currently online.

        Returns:
            List of AlexaDevice objects with online=True
        """
        return [device for device in self.devices.values() if device.online]

    @property
    def controllable_devices(self) -> list[AlexaDevice]:
        """Get list of devices with controllable capabilities.

        Returns:
            List of AlexaDevice objects that can be controlled
        """
        return [device for device in self.available_devices if device.is_controllable]
