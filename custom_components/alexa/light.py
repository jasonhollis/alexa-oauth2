"""Light platform for Alexa OAuth2 integration.

This module provides Home Assistant light entities for Alexa devices that
support the BrightnessController and/or ColorController interfaces.

Features:
- On/off control (via PowerController)
- Brightness control (0-254)
- Color control (RGB/HSV)
- Color temperature control (mireds)
- Integration with device registry
- Graceful error handling

Device Type Detection:
- Devices with BrightnessController → dimmable light
- Devices with ColorController → RGB light
- Devices with ColorTemperatureController → Color temperature light
- Devices with both → Full-featured light with all modes
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
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


def _get_light_color_modes(device: AlexaDevice) -> set[ColorMode]:
    """Determine color modes for a light device.

    Args:
        device: AlexaDevice to analyze

    Returns:
        Set of supported ColorMode values
    """
    modes = {ColorMode.ONOFF}  # All lights support on/off

    if device.supports_capability(AlexaInterface.BRIGHTNESS_CONTROLLER):
        modes.add(ColorMode.BRIGHTNESS)

    if device.supports_capability(AlexaInterface.COLOR_CONTROLLER):
        modes.add(ColorMode.HS)

    if device.supports_capability(AlexaInterface.COLOR_TEMPERATURE_CONTROLLER):
        modes.add(ColorMode.COLOR_TEMP)

    return modes


def _has_light_capabilities(device: AlexaDevice) -> bool:
    """Check if device is a light (has brightness or color control).

    Args:
        device: AlexaDevice to check

    Returns:
        True if device can be controlled as a light
    """
    return (
        device.supports_capability(AlexaInterface.POWER_CONTROLLER)
        and (
            device.supports_capability(AlexaInterface.BRIGHTNESS_CONTROLLER)
            or device.supports_capability(AlexaInterface.COLOR_CONTROLLER)
            or device.supports_capability(AlexaInterface.COLOR_TEMPERATURE_CONTROLLER)
        )
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa light platform from config entry.

    Called by Home Assistant when the light platform is loaded.
    Creates light entities for all Alexa devices with brightness/color control.

    Args:
        hass: Home Assistant instance
        config_entry: ConfigEntry for this integration
        async_add_entities: Callback to add entities
    """
    # Get coordinator from hass.data
    coordinator: AlexaDeviceCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]

    # Filter devices with light capabilities
    light_devices = [device for device in coordinator.devices.values() if _has_light_capabilities(device)]

    _LOGGER.debug(f"Creating {len(light_devices)} light entities")

    # Create light entities
    entities = [AlexaLightEntity(coordinator, device) for device in light_devices]

    # Register entities
    async_add_entities(entities)
    _LOGGER.info(f"Added {len(entities)} light entities")


class AlexaLightEntity(CoordinatorEntity[AlexaDeviceCoordinator], LightEntity):
    """Represents an Alexa device that can be used as a light.

    Supports brightness, color (RGB/HSV), and color temperature control.

    Integrates with:
    - DataUpdateCoordinator for automatic state updates
    - Home Assistant device registry
    - Home Assistant entity registry
    """

    _attr_has_entity_name = True
    _attr_translation_key = "alexa_light"

    def __init__(self, coordinator: AlexaDeviceCoordinator, device: AlexaDevice) -> None:
        """Initialize light entity.

        Args:
            coordinator: Device coordinator for updates
            device: The Alexa device this entity represents
        """
        super().__init__(coordinator)
        self._device_id = device.id
        self._attr_unique_id = f"alexa_light_{device.id}"
        self._attr_supported_color_modes = _get_light_color_modes(device)

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
    def brightness(self) -> int | None:
        """Get current brightness (0-254).

        Returns:
            Brightness level or None if not supported
        """
        if not self._device.supports_capability(AlexaInterface.BRIGHTNESS_CONTROLLER):
            return None

        brightness = self._device.state.get("brightness")
        if brightness is not None:
            return int(brightness)
        return None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Get current color (hue, saturation).

        Returns:
            Tuple of (hue 0-360, saturation 0-100) or None if not supported
        """
        if not self._device.supports_capability(AlexaInterface.COLOR_CONTROLLER):
            return None

        hue = self._device.state.get("hue")
        saturation = self._device.state.get("saturation")

        if hue is not None and saturation is not None:
            return (hue, saturation)
        return None

    @property
    def color_temp(self) -> int | None:
        """Get current color temperature (mireds).

        Returns:
            Color temperature in mireds (153-500) or None if not supported
        """
        if not self._device.supports_capability(AlexaInterface.COLOR_TEMPERATURE_CONTROLLER):
            return None

        mireds = self._device.state.get("colorTemperature")
        if mireds is not None:
            return int(mireds)
        return None

    @property
    def min_mireds(self) -> int:
        """Get minimum color temperature (cool white)."""
        return 153  # 6500K

    @property
    def max_mireds(self) -> int:
        """Get maximum color temperature (warm white)."""
        return 500  # 2000K

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
        """Turn on the light.

        Calls Alexa API to turn on the device. If brightness or color is specified,
        sets those values. Requests immediate state update for responsive UX.

        Args:
            **kwargs: Additional arguments including:
                brightness: Brightness 0-254
                hs_color: Tuple of (hue, saturation)
                color_temp: Color temperature in mireds

        Raises:
            AlexaAPIException: On API errors (handled by HA)
        """
        _LOGGER.debug(f"Turning on {self._device.name}")

        # Turn on the device
        await self.coordinator.api_client.set_power_state(self._device_id, turn_on=True)

        # Set brightness if specified
        if ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
            await self.coordinator.api_client.set_brightness(self._device_id, brightness)

        # Set color if specified
        if ATTR_HS_COLOR in kwargs:
            hue, saturation = kwargs[ATTR_HS_COLOR]
            # Brightness from HSV - use current or 100%
            brightness = kwargs.get(ATTR_BRIGHTNESS, 254)
            await self.coordinator.api_client.set_color(
                self._device_id, int(hue), int(saturation), int(brightness / 2.54)
            )

        # Set color temperature if specified
        if ATTR_COLOR_TEMP in kwargs:
            mireds = kwargs[ATTR_COLOR_TEMP]
            await self.coordinator.api_client.set_color_temperature(self._device_id, int(mireds))

        # Request immediate refresh for responsive UX
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light.

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
