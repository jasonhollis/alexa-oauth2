"""Sensor platform for Alexa OAuth2 integration.

This module provides Home Assistant sensor entities for Alexa devices that
provide read-only state information.

Features:
- Temperature sensors (from thermostats or temperature sensors)
- Humidity sensors
- Contact sensors (door/window open/close)
- Motion sensors
- Battery level sensors
- Integration with device registry
- Graceful error handling

Device Type Detection:
- Devices with TemperatureSensor → temperature sensor
- Devices with HumiditySensor → humidity sensor
- Devices with ContactSensor → contact sensor
- Devices with MotionSensor → motion sensor
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    Platform,
    UnitOfTemperature,
    PERCENTAGE,
)
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


def _get_sensor_entities(device: AlexaDevice) -> list[tuple[str, str, str]]:
    """Determine which sensor entities to create for a device.

    Returns list of (entity_type, capability, description) tuples.

    Args:
        device: AlexaDevice to analyze

    Returns:
        List of sensor tuples to create
    """
    sensors = []

    # Temperature sensor (from TemperatureSensor interface)
    if device.supports_capability(AlexaInterface.TEMPERATURE_SENSOR):
        sensors.append(("temperature", AlexaInterface.TEMPERATURE_SENSOR, "Temperature"))

    # Humidity sensor (inferred from device state)
    if "humidity" in device.state:
        sensors.append(("humidity", "humidity", "Humidity"))

    # Contact sensor (door/window)
    if device.supports_capability(AlexaInterface.CONTACT_SENSOR):
        sensors.append(("contact", AlexaInterface.CONTACT_SENSOR, "Contact"))

    # Motion sensor
    if device.supports_capability(AlexaInterface.MOTION_SENSOR):
        sensors.append(("motion", AlexaInterface.MOTION_SENSOR, "Motion"))

    # Battery level (many wireless devices have this)
    if "batteryLevel" in device.state:
        sensors.append(("battery", "battery", "Battery"))

    return sensors


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Alexa sensor platform from config entry.

    Called by Home Assistant when the sensor platform is loaded.
    Creates sensor entities for all Alexa devices with sensor capabilities.

    Args:
        hass: Home Assistant instance
        config_entry: ConfigEntry for this integration
        async_add_entities: Callback to add entities
    """
    # Get coordinator from hass.data
    coordinator: AlexaDeviceCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]

    # Create sensor entities for all devices
    entities = []
    for device in coordinator.devices.values():
        for sensor_type, capability, description in _get_sensor_entities(device):
            entity = AlexaSensorEntity(coordinator, device, sensor_type, capability, description)
            entities.append(entity)

    _LOGGER.debug(f"Creating {len(entities)} sensor entities")

    # Register entities
    async_add_entities(entities)
    _LOGGER.info(f"Added {len(entities)} sensor entities")


class AlexaSensorEntity(CoordinatorEntity[AlexaDeviceCoordinator], SensorEntity):
    """Represents an Alexa sensor (read-only state).

    Supports temperature, humidity, contact, motion, and battery sensors.

    Integrates with:
    - DataUpdateCoordinator for automatic state updates
    - Home Assistant device registry
    - Home Assistant entity registry
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AlexaDeviceCoordinator,
        device: AlexaDevice,
        sensor_type: str,
        capability: str,
        description: str,
    ) -> None:
        """Initialize sensor entity.

        Args:
            coordinator: Device coordinator for updates
            device: The Alexa device this entity represents
            sensor_type: Type of sensor (temperature, humidity, contact, motion, battery)
            capability: Alexa capability interface or state key
            description: Human-readable description
        """
        super().__init__(coordinator)
        self._device_id = device.id
        self._sensor_type = sensor_type
        self._capability = capability
        self._description = description
        self._attr_unique_id = f"alexa_{sensor_type}_{device.id}"
        self._attr_translation_key = f"alexa_{sensor_type}"

        # Set device class and unit based on sensor type
        if sensor_type == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif sensor_type == "humidity":
            self._attr_device_class = SensorDeviceClass.HUMIDITY
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif sensor_type == "contact":
            self._attr_device_class = SensorDeviceClass.ENUM
        elif sensor_type == "motion":
            self._attr_device_class = SensorDeviceClass.ENUM
        elif sensor_type == "battery":
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT

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
            Device display name + sensor type
        """
        return f"{self._device.display_name} {self._description}"

    @property
    def native_value(self) -> Any:
        """Get current sensor value.

        Returns:
            Sensor value based on type
        """
        if self._sensor_type == "temperature":
            temp = self._device.state.get("currentTemperature")
            return float(temp) if temp is not None else None

        elif self._sensor_type == "humidity":
            humidity = self._device.state.get("humidity")
            return int(humidity) if humidity is not None else None

        elif self._sensor_type == "contact":
            contact_state = self._device.state.get("contactDetectionState", "").upper()
            return "on" if contact_state == "DETECTED" else "off"

        elif self._sensor_type == "motion":
            motion_state = self._device.state.get("motionDetectionState", "").upper()
            return "on" if motion_state == "MOTION" else "off"

        elif self._sensor_type == "battery":
            battery = self._device.state.get("batteryLevel")
            return int(battery) if battery is not None else None

        return None

    @property
    def available(self) -> bool:
        """Check if entity is available.

        Entity is available if:
        1. Device is online in Alexa
        2. Coordinator has successfully updated

        Returns:
            True if entity data is valid
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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator.

        Called automatically when coordinator data changes.
        Updates entity state based on new device data.
        """
        self.async_write_ha_state()
