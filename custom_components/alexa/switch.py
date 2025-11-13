"""Switch platform for Alexa OAuth2 integration.

This module provides Home Assistant switch entities for Alexa devices that
support the PowerController interface (can be turned on/off).

Features:
- Automatic state sync with Alexa devices
- On/off control via Home Assistant switch service
- Integration with device registry
- Graceful error handling
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AlexaDeviceCoordinator
from .models import AlexaDevice, AlexaInterface

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.device_registry import DeviceInfo

_LOGGER = logging.getLogger(__name__)


def _has_power_controller(device: AlexaDevice) -> bool:
    """Check if device has PowerController capability.

    Args:
        device: AlexaDevice to check

    Returns:
        True if device can be turned on/off
    """
    return device.supports_capability(AlexaInterface.POWER_CONTROLLER)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa switch platform from config entry.

    Called by Home Assistant when the switch platform is loaded.
    Creates switch entities for all Alexa devices with PowerController capability.

    Args:
        hass: Home Assistant instance
        config_entry: ConfigEntry for this integration
        async_add_entities: Callback to add entities
    """
    # Get coordinator from hass.data
    coordinator: AlexaDeviceCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]

    # Filter devices with PowerController capability
    switch_devices = [device for device in coordinator.devices.values() if _has_power_controller(device)]

    _LOGGER.debug(f"Creating {len(switch_devices)} switch entities")

    # Create switch entities
    entities = [AlexaSwitchEntity(coordinator, device) for device in switch_devices]

    # Register entities
    async_add_entities(entities)
    _LOGGER.info(f"Added {len(entities)} switch entities")


class AlexaSwitchEntity(CoordinatorEntity[AlexaDeviceCoordinator], SwitchEntity):
    """Represents an Alexa device that can be turned on/off.

    Integrates with:
    - DataUpdateCoordinator for automatic state updates
    - Home Assistant device registry
    - Home Assistant entity registry
    """

    _attr_has_entity_name = True
    _attr_translation_key = "alexa_switch"

    def __init__(self, coordinator: AlexaDeviceCoordinator, device: AlexaDevice) -> None:
        """Initialize switch entity.

        Args:
            coordinator: Device coordinator for updates
            device: The Alexa device this entity represents
        """
        super().__init__(coordinator)
        self._device_id = device.id
        self._attr_unique_id = f"alexa_switch_{device.id}"

    @property
    def _device(self) -> AlexaDevice:
        """Get the device object from coordinator.

        Returns:
            AlexaDevice instance
        """
        return self.coordinator.devices[self._device_id]

    @property
    def name(self) -> str:
        """Get entity name for UI.

        Returns:
            Device display name (e.g., "Living Room Light (Philips)")
        """
        return self._device.display_name

    @property
    def is_on(self) -> bool:
        """Get current power state.

        Returns:
            True if device is ON, False if OFF
        """
        return self._device.get_power_state()

    @property
    def available(self) -> bool:
        """Check if entity is available.

        Entity is available if:
        1. Device is online in Alexa
        2. Coordinator has successfully updated

        Returns:
            True if entity can be controlled
        """
        return self._device.online and self.coordinator.last_update_success

    @property
    def device_info(self) -> DeviceInfo:
        """Get device registry info.

        Links entity to device in device registry for grouping and management.

        Returns:
            DeviceInfo dictionary
        """
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device.display_name,
            "manufacturer": self._device.manufacturer_name,
            "model": self._device.model_name,
        }

    @property
    def should_poll(self) -> bool:
        """Disable polling - coordinator handles updates.

        Returns:
            False (coordinator polls for us)
        """
        return False

    @property
    def assumed_state(self) -> bool:
        """We have actual device state, not guessed.

        Returns:
            False (we have real state from API)
        """
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch.

        Calls Alexa API to turn on the device and requests immediate state update.

        Args:
            **kwargs: Additional arguments (unused)

        Raises:
            AlexaAPIException: On API errors (handled by HA)
        """
        _LOGGER.debug(f"Turning on {self._device.name}")
        await self.coordinator.api_client.set_power_state(self._device_id, turn_on=True)
        # Request immediate refresh for responsive UX
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch.

        Calls Alexa API to turn off the device and requests immediate state update.

        Args:
            **kwargs: Additional arguments (unused)

        Raises:
            AlexaAPIException: On API errors (handled by HA)
        """
        _LOGGER.debug(f"Turning off {self._device.name}")
        await self.coordinator.api_client.set_power_state(self._device_id, turn_on=False)
        # Request immediate refresh for responsive UX
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator.

        Called automatically when coordinator data changes.
        Updates entity state based on new device data.
        """
        self.async_write_ha_state()
