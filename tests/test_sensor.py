"""Tests for Alexa sensor platform (Phase 3).

Test Coverage:
- Entity creation for temperature, humidity, contact, motion, battery sensors
- State parsing and unit conversion
- Read-only sensor behavior
- Error handling and availability
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant

from custom_components.alexa.sensor import (
    AlexaSensorEntity,
    _get_sensor_entities,
    async_setup_entry,
)
from custom_components.alexa.models import AlexaDevice, AlexaInterface, AlexaCapability
from custom_components.alexa.coordinator import AlexaDeviceCoordinator


@pytest.fixture
def temperature_sensor_device():
    """Create a temperature sensor device."""
    return AlexaDevice(
        id="sensor-temp-001",
        name="Room Temperature",
        device_type="TEMPERATURE_SENSOR",
        online=True,
        capabilities=[
            AlexaCapability(interface=AlexaInterface.TEMPERATURE_SENSOR, version="3"),
        ],
        state={"currentTemperature": 22.5},
        manufacturer_name="Eve",
        model_name="Room",
    )


@pytest.fixture
def humidity_device():
    """Create a device with humidity."""
    return AlexaDevice(
        id="sensor-humidity-001",
        name="Humidity Monitor",
        device_type="SENSOR",
        online=True,
        capabilities=[],
        state={"humidity": 65},
        manufacturer_name="Eve",
        model_name="Room Plus",
    )


@pytest.fixture
def contact_sensor_device():
    """Create a contact sensor device (door/window)."""
    return AlexaDevice(
        id="sensor-contact-001",
        name="Front Door",
        device_type="DOOR_SENSOR",
        online=True,
        capabilities=[
            AlexaCapability(interface=AlexaInterface.CONTACT_SENSOR, version="3"),
        ],
        state={"contactDetectionState": "DETECTED"},
        manufacturer_name="Eve",
        model_name="Door",
    )


@pytest.fixture
def motion_sensor_device():
    """Create a motion sensor device."""
    return AlexaDevice(
        id="sensor-motion-001",
        name="Motion Detector",
        device_type="MOTION_SENSOR",
        online=True,
        capabilities=[
            AlexaCapability(interface=AlexaInterface.MOTION_SENSOR, version="3"),
        ],
        state={"motionDetectionState": "MOTION"},
        manufacturer_name="Eve",
        model_name="Motion",
    )


@pytest.fixture
def battery_device():
    """Create a wireless device with battery."""
    return AlexaDevice(
        id="sensor-battery-001",
        name="Wireless Light",
        device_type="LIGHT",
        online=True,
        capabilities=[
            AlexaCapability(interface=AlexaInterface.POWER_CONTROLLER, version="3"),
            AlexaCapability(interface=AlexaInterface.BRIGHTNESS_CONTROLLER, version="3"),
        ],
        state={"powerState": "ON", "brightness": 100, "batteryLevel": 85},
        manufacturer_name="Eve",
        model_name="Outdoor Light",
    )


@pytest.fixture
def mock_coordinator(temperature_sensor_device, humidity_device, contact_sensor_device, motion_sensor_device, battery_device):
    """Create a mock coordinator with test devices."""
    coordinator = AsyncMock(spec=AlexaDeviceCoordinator)
    coordinator.devices = {
        temperature_sensor_device.id: temperature_sensor_device,
        humidity_device.id: humidity_device,
        contact_sensor_device.id: contact_sensor_device,
        motion_sensor_device.id: motion_sensor_device,
        battery_device.id: battery_device,
    }
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


class TestSensorDetection:
    """Test detection of sensor capabilities."""

    def test_temperature_sensor_detected(self, temperature_sensor_device):
        """Test detection of temperature sensor."""
        sensors = _get_sensor_entities(temperature_sensor_device)
        assert any(s[0] == "temperature" for s in sensors)

    def test_humidity_detected(self, humidity_device):
        """Test detection of humidity sensor."""
        sensors = _get_sensor_entities(humidity_device)
        assert any(s[0] == "humidity" for s in sensors)

    def test_contact_sensor_detected(self, contact_sensor_device):
        """Test detection of contact sensor."""
        sensors = _get_sensor_entities(contact_sensor_device)
        assert any(s[0] == "contact" for s in sensors)

    def test_motion_sensor_detected(self, motion_sensor_device):
        """Test detection of motion sensor."""
        sensors = _get_sensor_entities(motion_sensor_device)
        assert any(s[0] == "motion" for s in sensors)

    def test_battery_detected(self, battery_device):
        """Test detection of battery sensor."""
        sensors = _get_sensor_entities(battery_device)
        assert any(s[0] == "battery" for s in sensors)


class TestAlexaSensorEntity:
    """Test AlexaSensorEntity state and behavior."""

    def test_temperature_entity_creation(self, mock_coordinator, temperature_sensor_device):
        """Test temperature sensor entity creation."""
        entity = AlexaSensorEntity(
            mock_coordinator,
            temperature_sensor_device,
            "temperature",
            AlexaInterface.TEMPERATURE_SENSOR,
            "Temperature",
        )

        assert entity._device_id == temperature_sensor_device.id
        assert entity._sensor_type == "temperature"
        assert entity._attr_device_class == SensorDeviceClass.TEMPERATURE
        assert entity._attr_native_unit_of_measurement == UnitOfTemperature.CELSIUS

    def test_humidity_entity_creation(self, mock_coordinator, humidity_device):
        """Test humidity sensor entity creation."""
        entity = AlexaSensorEntity(
            mock_coordinator,
            humidity_device,
            "humidity",
            "humidity",
            "Humidity",
        )

        assert entity._sensor_type == "humidity"
        assert entity._attr_device_class == SensorDeviceClass.HUMIDITY
        assert entity._attr_native_unit_of_measurement == PERCENTAGE

    def test_contact_entity_creation(self, mock_coordinator, contact_sensor_device):
        """Test contact sensor entity creation."""
        entity = AlexaSensorEntity(
            mock_coordinator,
            contact_sensor_device,
            "contact",
            AlexaInterface.CONTACT_SENSOR,
            "Contact",
        )

        assert entity._sensor_type == "contact"
        assert entity._attr_device_class == SensorDeviceClass.ENUM

    def test_motion_entity_creation(self, mock_coordinator, motion_sensor_device):
        """Test motion sensor entity creation."""
        entity = AlexaSensorEntity(
            mock_coordinator,
            motion_sensor_device,
            "motion",
            AlexaInterface.MOTION_SENSOR,
            "Motion",
        )

        assert entity._sensor_type == "motion"
        assert entity._attr_device_class == SensorDeviceClass.ENUM

    def test_battery_entity_creation(self, mock_coordinator, battery_device):
        """Test battery sensor entity creation."""
        entity = AlexaSensorEntity(
            mock_coordinator,
            battery_device,
            "battery",
            "battery",
            "Battery",
        )

        assert entity._sensor_type == "battery"
        assert entity._attr_device_class == SensorDeviceClass.BATTERY
        assert entity._attr_native_unit_of_measurement == PERCENTAGE


class TestSensorValues:
    """Test sensor value reading."""

    def test_temperature_value(self, mock_coordinator, temperature_sensor_device):
        """Test temperature value reading."""
        temperature_sensor_device.state["currentTemperature"] = 22.5
        entity = AlexaSensorEntity(
            mock_coordinator,
            temperature_sensor_device,
            "temperature",
            AlexaInterface.TEMPERATURE_SENSOR,
            "Temperature",
        )

        assert entity.native_value == 22.5

    def test_humidity_value(self, mock_coordinator, humidity_device):
        """Test humidity value reading."""
        humidity_device.state["humidity"] = 65
        entity = AlexaSensorEntity(
            mock_coordinator,
            humidity_device,
            "humidity",
            "humidity",
            "Humidity",
        )

        assert entity.native_value == 65

    def test_contact_open(self, mock_coordinator, contact_sensor_device):
        """Test contact sensor when open."""
        contact_sensor_device.state["contactDetectionState"] = "DETECTED"
        entity = AlexaSensorEntity(
            mock_coordinator,
            contact_sensor_device,
            "contact",
            AlexaInterface.CONTACT_SENSOR,
            "Contact",
        )

        assert entity.native_value == "on"

    def test_contact_closed(self, mock_coordinator, contact_sensor_device):
        """Test contact sensor when closed."""
        contact_sensor_device.state["contactDetectionState"] = "NOT_DETECTED"
        entity = AlexaSensorEntity(
            mock_coordinator,
            contact_sensor_device,
            "contact",
            AlexaInterface.CONTACT_SENSOR,
            "Contact",
        )

        assert entity.native_value == "off"

    def test_motion_detected(self, mock_coordinator, motion_sensor_device):
        """Test motion sensor when motion detected."""
        motion_sensor_device.state["motionDetectionState"] = "MOTION"
        entity = AlexaSensorEntity(
            mock_coordinator,
            motion_sensor_device,
            "motion",
            AlexaInterface.MOTION_SENSOR,
            "Motion",
        )

        assert entity.native_value == "on"

    def test_motion_no_motion(self, mock_coordinator, motion_sensor_device):
        """Test motion sensor when no motion."""
        motion_sensor_device.state["motionDetectionState"] = "NO_MOTION"
        entity = AlexaSensorEntity(
            mock_coordinator,
            motion_sensor_device,
            "motion",
            AlexaInterface.MOTION_SENSOR,
            "Motion",
        )

        assert entity.native_value == "off"

    def test_battery_value(self, mock_coordinator, battery_device):
        """Test battery value reading."""
        battery_device.state["batteryLevel"] = 85
        entity = AlexaSensorEntity(
            mock_coordinator,
            battery_device,
            "battery",
            "battery",
            "Battery",
        )

        assert entity.native_value == 85


class TestSensorAvailability:
    """Test sensor availability."""

    def test_available_when_online(self, mock_coordinator, temperature_sensor_device):
        """Test sensor is available when online."""
        temperature_sensor_device.online = True
        mock_coordinator.last_update_success = True
        entity = AlexaSensorEntity(
            mock_coordinator,
            temperature_sensor_device,
            "temperature",
            AlexaInterface.TEMPERATURE_SENSOR,
            "Temperature",
        )

        assert entity.available is True

    def test_unavailable_when_offline(self, mock_coordinator, temperature_sensor_device):
        """Test sensor is unavailable when offline."""
        temperature_sensor_device.online = False
        entity = AlexaSensorEntity(
            mock_coordinator,
            temperature_sensor_device,
            "temperature",
            AlexaInterface.TEMPERATURE_SENSOR,
            "Temperature",
        )

        assert entity.available is False

    def test_device_info(self, mock_coordinator, temperature_sensor_device):
        """Test device registry info."""
        entity = AlexaSensorEntity(
            mock_coordinator,
            temperature_sensor_device,
            "temperature",
            AlexaInterface.TEMPERATURE_SENSOR,
            "Temperature",
        )
        device_info = entity.device_info

        assert device_info["identifiers"] == {("alexa", temperature_sensor_device.id)}
        assert device_info["manufacturer"] == "Eve"


class TestSensorPlatformSetup:
    """Test sensor platform setup."""

    @pytest.mark.asyncio
    async def test_setup_creates_sensor_entities(self, mock_coordinator):
        """Test that setup creates sensor entities for all devices."""
        hass = AsyncMock(spec=HomeAssistant)
        config_entry = MagicMock()
        config_entry.entry_id = "test-entry"
        async_add_entities = AsyncMock()

        hass.data = {"alexa": {"test-entry": {"coordinator": mock_coordinator}}}

        await async_setup_entry(hass, config_entry, async_add_entities)

        assert async_add_entities.call_count == 1
        entities = async_add_entities.call_args[0][0]
        # Should create sensors for: temperature (1), humidity (1), contact (1), motion (1), battery (1) = 5
        assert len(entities) == 5
        assert all(isinstance(e, AlexaSensorEntity) for e in entities)
