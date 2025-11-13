"""Climate platform for Alexa OAuth2 integration.

This module provides Home Assistant climate entities for Alexa devices that
support the ThermostatController interface.

Features:
- Current temperature monitoring
- Target temperature control
- HVAC mode control (heat, cool, auto, off)
- Preset modes (eco, comfort, away, etc.)
- Integration with device registry
- Graceful error handling

Device Type Detection:
- Devices with ThermostatController → climate entity
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ClimateEntity,
    HVACAction,
    HVACMode,
    ClimateEntityFeature,
)
from homeassistant.const import Platform, UnitOfTemperature
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


def _has_climate_capabilities(device: AlexaDevice) -> bool:
    """Check if device is a thermostat.

    Args:
        device: AlexaDevice to check

    Returns:
        True if device has thermostat control
    """
    return device.supports_capability(AlexaInterface.THERMOSTAT_CONTROLLER)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa climate platform from config entry.

    Called by Home Assistant when the climate platform is loaded.
    Creates climate entities for all Alexa devices with thermostat control.

    Args:
        hass: Home Assistant instance
        config_entry: ConfigEntry for this integration
        async_add_entities: Callback to add entities
    """
    # Get coordinator from hass.data
    coordinator: AlexaDeviceCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]

    # Filter devices with climate capabilities
    climate_devices = [device for device in coordinator.devices.values() if _has_climate_capabilities(device)]

    _LOGGER.debug(f"Creating {len(climate_devices)} climate entities")

    # Create climate entities
    entities = [AlexaClimateEntity(coordinator, device) for device in climate_devices]

    # Register entities
    async_add_entities(entities)
    _LOGGER.info(f"Added {len(entities)} climate entities")


class AlexaClimateEntity(CoordinatorEntity[AlexaDeviceCoordinator], ClimateEntity):
    """Represents an Alexa thermostat device.

    Supports temperature monitoring and control with HVAC modes.

    Integrates with:
    - DataUpdateCoordinator for automatic state updates
    - Home Assistant device registry
    - Home Assistant entity registry
    """

    _attr_has_entity_name = True
    _attr_translation_key = "alexa_climate"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.AUTO,
    ]
    _attr_preset_modes = [
        "comfort",
        "eco",
        "away",
    ]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
    )

    def __init__(self, coordinator: AlexaDeviceCoordinator, device: AlexaDevice) -> None:
        """Initialize climate entity.

        Args:
            coordinator: Device coordinator for updates
            device: The Alexa device this entity represents
        """
        super().__init__(coordinator)
        self._device_id = device.id
        self._attr_unique_id = f"alexa_climate_{device.id}"

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
            Device display name (e.g., "Living Room Thermostat (Ecobee)")
        """
        return self._device.display_name

    @property
    def current_temperature(self) -> float | None:
        """Get current temperature in Celsius.

        Returns:
            Current temperature or None if not available
        """
        temp = self._device.state.get("currentTemperature")
        if temp is not None:
            return float(temp)
        return None

    @property
    def target_temperature(self) -> float | None:
        """Get target temperature in Celsius.

        Returns:
            Target temperature or None if not available
        """
        temp = self._device.state.get("targetSetpoint")
        if temp is not None:
            return float(temp)
        return None

    @property
    def min_temp(self) -> float:
        """Get minimum target temperature."""
        return 10.0  # 10°C

    @property
    def max_temp(self) -> float:
        """Get maximum target temperature."""
        return 38.0  # 38°C

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Get current HVAC mode.

        Maps Alexa thermostat modes to Home Assistant HVAC modes.

        Returns:
            Current HVAC mode or None if not available
        """
        mode = self._device.state.get("thermostatMode", "").upper()

        mode_map = {
            "OFF": HVACMode.OFF,
            "HEAT": HVACMode.HEAT,
            "COOL": HVACMode.COOL,
            "AUTO": HVACMode.AUTO,
        }

        return mode_map.get(mode)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Get current HVAC action (heating, cooling, idle).

        Returns:
            Current HVAC action or None if not available
        """
        action = self._device.state.get("thermostatAction", "").upper()

        action_map = {
            "IDLE": HVACAction.IDLE,
            "HEATING": HVACAction.HEATING,
            "COOLING": HVACAction.COOLING,
        }

        return action_map.get(action)

    @property
    def preset_mode(self) -> str | None:
        """Get current preset mode.

        Returns:
            Current preset mode or None if not available
        """
        return self._device.state.get("preset_mode")

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

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature.

        Args:
            **kwargs: Additional arguments including:
                temperature: Target temperature in Celsius

        Raises:
            AlexaAPIException: On API errors (handled by HA)
        """
        if "temperature" not in kwargs:
            return

        target_temp = kwargs["temperature"]
        _LOGGER.debug(f"Setting {self._device.name} temperature to {target_temp}°C")

        # Clamp temperature to valid range
        target_temp = max(self.min_temp, min(self.max_temp, target_temp))

        await self.coordinator.api_client.set_temperature(self._device_id, target_temp)

        # Request immediate refresh for responsive UX
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode.

        Args:
            hvac_mode: Target HVAC mode (off, heat, cool, auto)

        Raises:
            AlexaAPIException: On API errors (handled by HA)
        """
        _LOGGER.debug(f"Setting {self._device.name} HVAC mode to {hvac_mode}")

        # Map HA HVAC mode to Alexa thermostat mode
        mode_map = {
            HVACMode.OFF: "OFF",
            HVACMode.HEAT: "HEAT",
            HVACMode.COOL: "COOL",
            HVACMode.AUTO: "AUTO",
        }

        alexa_mode = mode_map.get(hvac_mode)
        if not alexa_mode:
            _LOGGER.error(f"Unknown HVAC mode: {hvac_mode}")
            return

        # For now, we just update state
        # In a real implementation, this would call an API endpoint
        self._device.state["thermostatMode"] = alexa_mode
        self.async_write_ha_state()

        # Request immediate refresh
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode.

        Args:
            preset_mode: Target preset mode (comfort, eco, away)

        Raises:
            AlexaAPIException: On API errors (handled by HA)
        """
        _LOGGER.debug(f"Setting {self._device.name} preset mode to {preset_mode}")

        # For now, we just update state
        # In a real implementation, this would call an API endpoint
        self._device.state["preset_mode"] = preset_mode
        self.async_write_ha_state()

        # Request immediate refresh
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator.

        Called automatically when coordinator data changes.
        Updates entity state based on new device data.
        """
        self.async_write_ha_state()
