"""Tests for Alexa light platform (Phase 3).

Test Coverage:
- Entity creation from devices with brightness/color capabilities
- State parsing (power, brightness, RGB, mireds)
- Commands (turn_on/off, set_brightness, set_color, set_color_temp)
- Color mode detection
- Error handling and availability
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.components.light import ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.const import STATE_ON, STATE_OFF
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.alexa.light import (
    AlexaLightEntity,
    _has_light_capabilities,
    _get_light_color_modes,
    async_setup_entry,
)
from custom_components.alexa.models import AlexaDevice, AlexaInterface, AlexaCapability
from custom_components.alexa.coordinator import AlexaDeviceCoordinator


@pytest.fixture
def brightness_device():
    """Create a dimmable light device."""
    return AlexaDevice(
        id="light-brightness-001",
        name="Dimmable Light",
        device_type="LIGHT",
        online=True,
        capabilities=[
            AlexaCapability(interface=AlexaInterface.POWER_CONTROLLER, version="3"),
            AlexaCapability(interface=AlexaInterface.BRIGHTNESS_CONTROLLER, version="3"),
        ],
        state={"powerState": "ON", "brightness": 127},
        manufacturer_name="Philips",
        model_name="Hue Light",
    )


@pytest.fixture
def color_device():
    """Create a full-featured RGB light device."""
    return AlexaDevice(
        id="light-color-001",
        name="RGB Light",
        device_type="LIGHT",
        online=True,
        capabilities=[
            AlexaCapability(interface=AlexaInterface.POWER_CONTROLLER, version="3"),
            AlexaCapability(interface=AlexaInterface.BRIGHTNESS_CONTROLLER, version="3"),
            AlexaCapability(interface=AlexaInterface.COLOR_CONTROLLER, version="3"),
            AlexaCapability(interface=AlexaInterface.COLOR_TEMPERATURE_CONTROLLER, version="3"),
        ],
        state={
            "powerState": "ON",
            "brightness": 200,
            "hue": 120,
            "saturation": 75,
            "colorTemperature": 300,
        },
        manufacturer_name="LIFX",
        model_name="Color A19",
    )


@pytest.fixture
def color_temp_device():
    """Create a color temperature light device."""
    return AlexaDevice(
        id="light-ct-001",
        name="Color Temp Light",
        device_type="LIGHT",
        online=True,
        capabilities=[
            AlexaCapability(interface=AlexaInterface.POWER_CONTROLLER, version="3"),
            AlexaCapability(interface=AlexaInterface.COLOR_TEMPERATURE_CONTROLLER, version="3"),
        ],
        state={"powerState": "ON", "colorTemperature": 250},
        manufacturer_name="Nanoleaf",
        model_name="Panel",
    )


@pytest.fixture
def mock_coordinator(brightness_device, color_device, color_temp_device):
    """Create a mock coordinator with test devices."""
    coordinator = AsyncMock(spec=AlexaDeviceCoordinator)
    coordinator.devices = {
        brightness_device.id: brightness_device,
        color_device.id: color_device,
        color_temp_device.id: color_temp_device,
    }
    coordinator.api_client = AsyncMock()
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


class TestLightCapabilityDetection:
    """Test detection of devices with light capabilities."""

    def test_brightness_device_detected(self, brightness_device):
        """Test that brightness-only device is detected as light."""
        assert _has_light_capabilities(brightness_device) is True

    def test_color_device_detected(self, color_device):
        """Test that color device is detected as light."""
        assert _has_light_capabilities(color_device) is True

    def test_color_temp_device_detected(self, color_temp_device):
        """Test that color temp device is detected as light."""
        assert _has_light_capabilities(color_temp_device) is True

    def test_switch_device_not_detected(self):
        """Test that on/off switch is not detected as light."""
        device = AlexaDevice(
            id="switch-001",
            name="Switch",
            device_type="SWITCH",
            online=True,
            capabilities=[
                AlexaCapability(interface=AlexaInterface.POWER_CONTROLLER, version="3"),
            ],
            state={"powerState": "ON"},
        )
        assert _has_light_capabilities(device) is False


class TestLightColorModes:
    """Test color mode detection."""

    def test_brightness_only_modes(self, brightness_device):
        """Test color modes for brightness-only light."""
        modes = _get_light_color_modes(brightness_device)
        assert ColorMode.ONOFF in modes
        assert ColorMode.BRIGHTNESS in modes
        assert ColorMode.HS not in modes
        assert ColorMode.COLOR_TEMP not in modes

    def test_full_color_modes(self, color_device):
        """Test color modes for full-featured light."""
        modes = _get_light_color_modes(color_device)
        assert ColorMode.ONOFF in modes
        assert ColorMode.BRIGHTNESS in modes
        assert ColorMode.HS in modes
        assert ColorMode.COLOR_TEMP in modes

    def test_color_temp_only_modes(self, color_temp_device):
        """Test color modes for color temp light."""
        modes = _get_light_color_modes(color_temp_device)
        assert ColorMode.ONOFF in modes
        assert ColorMode.BRIGHTNESS not in modes
        assert ColorMode.HS not in modes
        assert ColorMode.COLOR_TEMP in modes


class TestAlexaLightEntity:
    """Test AlexaLightEntity state and behavior."""

    def test_entity_creation(self, mock_coordinator, brightness_device):
        """Test entity creation and initialization."""
        entity = AlexaLightEntity(mock_coordinator, brightness_device)

        assert entity._device_id == brightness_device.id
        assert entity._attr_unique_id == f"alexa_light_{brightness_device.id}"
        assert entity.name == "Dimmable Light (Philips)"

    def test_entity_on_state(self, mock_coordinator, brightness_device):
        """Test that entity reports ON state correctly."""
        brightness_device.state["powerState"] = "ON"
        entity = AlexaLightEntity(mock_coordinator, brightness_device)

        assert entity.is_on is True

    def test_entity_off_state(self, mock_coordinator, brightness_device):
        """Test that entity reports OFF state correctly."""
        brightness_device.state["powerState"] = "OFF"
        entity = AlexaLightEntity(mock_coordinator, brightness_device)

        assert entity.is_on is False

    def test_brightness_reading(self, mock_coordinator, brightness_device):
        """Test brightness property reading."""
        brightness_device.state["brightness"] = 150
        entity = AlexaLightEntity(mock_coordinator, brightness_device)

        assert entity.brightness == 150

    def test_brightness_missing_returns_none(self, mock_coordinator, color_temp_device):
        """Test that missing brightness returns None."""
        entity = AlexaLightEntity(mock_coordinator, color_temp_device)
        assert entity.brightness is None

    def test_color_reading(self, mock_coordinator, color_device):
        """Test HS color reading."""
        color_device.state["hue"] = 120
        color_device.state["saturation"] = 75
        entity = AlexaLightEntity(mock_coordinator, color_device)

        hs = entity.hs_color
        assert hs == (120, 75)

    def test_color_temp_reading(self, mock_coordinator, color_device):
        """Test color temperature reading in mireds."""
        color_device.state["colorTemperature"] = 300
        entity = AlexaLightEntity(mock_coordinator, color_device)

        assert entity.color_temp == 300

    def test_color_temp_range(self, mock_coordinator, color_temp_device):
        """Test color temperature range (mireds)."""
        entity = AlexaLightEntity(mock_coordinator, color_temp_device)

        assert entity.min_mireds == 153  # 6500K cool white
        assert entity.max_mireds == 500  # 2000K warm white

    def test_availability_when_online(self, mock_coordinator, brightness_device):
        """Test entity is available when device online."""
        brightness_device.online = True
        mock_coordinator.last_update_success = True
        entity = AlexaLightEntity(mock_coordinator, brightness_device)

        assert entity.available is True

    def test_unavailable_when_offline(self, mock_coordinator, brightness_device):
        """Test entity is unavailable when device offline."""
        brightness_device.online = False
        entity = AlexaLightEntity(mock_coordinator, brightness_device)

        assert entity.available is False

    def test_unavailable_when_coordinator_failed(self, mock_coordinator, brightness_device):
        """Test entity is unavailable when coordinator update failed."""
        brightness_device.online = True
        mock_coordinator.last_update_success = False
        entity = AlexaLightEntity(mock_coordinator, brightness_device)

        assert entity.available is False

    def test_device_info(self, mock_coordinator, brightness_device):
        """Test device registry info."""
        entity = AlexaLightEntity(mock_coordinator, brightness_device)
        device_info = entity.device_info

        assert device_info["identifiers"] == {("alexa", brightness_device.id)}
        assert device_info["name"] == "Dimmable Light (Philips)"
        assert device_info["manufacturer"] == "Philips"
        assert device_info["model"] == "Hue Light"


class TestAlexaLightCommands:
    """Test light entity command execution."""

    @pytest.mark.asyncio
    async def test_turn_on(self, mock_coordinator, brightness_device):
        """Test turn on command."""
        entity = AlexaLightEntity(mock_coordinator, brightness_device)
        await entity.async_turn_on()

        mock_coordinator.api_client.set_power_state.assert_called_once_with(
            brightness_device.id, turn_on=True
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off(self, mock_coordinator, brightness_device):
        """Test turn off command."""
        entity = AlexaLightEntity(mock_coordinator, brightness_device)
        await entity.async_turn_off()

        mock_coordinator.api_client.set_power_state.assert_called_once_with(
            brightness_device.id, turn_on=False
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_brightness(self, mock_coordinator, brightness_device):
        """Test brightness control."""
        entity = AlexaLightEntity(mock_coordinator, brightness_device)
        await entity.async_turn_on(brightness=200)

        # Should set power and brightness
        mock_coordinator.api_client.set_power_state.assert_called_once()
        mock_coordinator.api_client.set_brightness.assert_called_once_with(
            brightness_device.id, 200
        )

    @pytest.mark.asyncio
    async def test_set_color(self, mock_coordinator, color_device):
        """Test color control."""
        entity = AlexaLightEntity(mock_coordinator, color_device)
        await entity.async_turn_on(hs_color=(120, 75), brightness=254)

        # Should set power and color
        mock_coordinator.api_client.set_power_state.assert_called_once()
        mock_coordinator.api_client.set_color.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_color_temp(self, mock_coordinator, color_temp_device):
        """Test color temperature control."""
        entity = AlexaLightEntity(mock_coordinator, color_temp_device)
        await entity.async_turn_on(color_temp=300)

        mock_coordinator.api_client.set_power_state.assert_called_once()
        mock_coordinator.api_client.set_color_temperature.assert_called_once_with(
            color_temp_device.id, 300
        )


class TestLightPlatformSetup:
    """Test light platform setup."""

    @pytest.mark.asyncio
    async def test_setup_creates_light_entities(self, mock_coordinator):
        """Test that setup creates light entities for supported devices."""
        hass = AsyncMock(spec=HomeAssistant)
        config_entry = MagicMock()
        config_entry.entry_id = "test-entry"
        async_add_entities = AsyncMock()

        hass.data = {"alexa": {"test-entry": {"coordinator": mock_coordinator}}}

        await async_setup_entry(hass, config_entry, async_add_entities)

        # Should create 3 light entities (all test devices have light capabilities)
        assert async_add_entities.call_count == 1
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 3
        assert all(isinstance(e, AlexaLightEntity) for e in entities)
