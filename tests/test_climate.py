"""Tests for Alexa climate platform (Phase 3).

Test Coverage:
- Entity creation from ThermostatController devices
- Temperature reading and control
- HVAC mode control (heat, cool, auto, off)
- Preset modes (comfort, eco, away)
- Error handling and availability
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.components.climate import HVACMode, HVACAction
from homeassistant.core import HomeAssistant

from custom_components.alexa.climate import (
    AlexaClimateEntity,
    _has_climate_capabilities,
    async_setup_entry,
)
from custom_components.alexa.models import AlexaDevice, AlexaInterface, AlexaCapability
from custom_components.alexa.coordinator import AlexaDeviceCoordinator


@pytest.fixture
def thermostat_device():
    """Create a thermostat device."""
    return AlexaDevice(
        id="climate-001",
        name="Smart Thermostat",
        device_type="THERMOSTAT",
        online=True,
        capabilities=[
            AlexaCapability(interface=AlexaInterface.THERMOSTAT_CONTROLLER, version="3"),
        ],
        state={
            "currentTemperature": 22.5,
            "targetSetpoint": 21.0,
            "thermostatMode": "HEAT",
            "thermostatAction": "HEATING",
            "preset_mode": "comfort",
        },
        manufacturer_name="Ecobee",
        model_name="SmartThermostat",
    )


@pytest.fixture
def mock_coordinator(thermostat_device):
    """Create a mock coordinator with thermostat device."""
    coordinator = AsyncMock(spec=AlexaDeviceCoordinator)
    coordinator.devices = {thermostat_device.id: thermostat_device}
    coordinator.api_client = AsyncMock()
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


class TestClimateCapabilityDetection:
    """Test detection of thermostat devices."""

    def test_thermostat_detected(self, thermostat_device):
        """Test that thermostat is detected."""
        assert _has_climate_capabilities(thermostat_device) is True

    def test_non_thermostat_not_detected(self):
        """Test that non-thermostat devices are not detected."""
        device = AlexaDevice(
            id="light-001",
            name="Light",
            device_type="LIGHT",
            online=True,
            capabilities=[
                AlexaCapability(interface=AlexaInterface.POWER_CONTROLLER, version="3"),
            ],
        )
        assert _has_climate_capabilities(device) is False


class TestAlexaClimateEntity:
    """Test AlexaClimateEntity state and behavior."""

    def test_entity_creation(self, mock_coordinator, thermostat_device):
        """Test entity creation and initialization."""
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity._device_id == thermostat_device.id
        assert entity.name == "Smart Thermostat (Ecobee)"

    def test_current_temperature_reading(self, mock_coordinator, thermostat_device):
        """Test reading current temperature."""
        thermostat_device.state["currentTemperature"] = 22.5
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.current_temperature == 22.5

    def test_target_temperature_reading(self, mock_coordinator, thermostat_device):
        """Test reading target temperature."""
        thermostat_device.state["targetSetpoint"] = 21.0
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.target_temperature == 21.0

    def test_temperature_range(self, mock_coordinator, thermostat_device):
        """Test temperature control limits."""
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.min_temp == 10.0  # 10°C
        assert entity.max_temp == 38.0  # 38°C

    def test_hvac_mode_heat(self, mock_coordinator, thermostat_device):
        """Test HVAC mode reading (heat)."""
        thermostat_device.state["thermostatMode"] = "HEAT"
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.hvac_mode == HVACMode.HEAT

    def test_hvac_mode_cool(self, mock_coordinator, thermostat_device):
        """Test HVAC mode reading (cool)."""
        thermostat_device.state["thermostatMode"] = "COOL"
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.hvac_mode == HVACMode.COOL

    def test_hvac_mode_auto(self, mock_coordinator, thermostat_device):
        """Test HVAC mode reading (auto)."""
        thermostat_device.state["thermostatMode"] = "AUTO"
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.hvac_mode == HVACMode.AUTO

    def test_hvac_mode_off(self, mock_coordinator, thermostat_device):
        """Test HVAC mode reading (off)."""
        thermostat_device.state["thermostatMode"] = "OFF"
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.hvac_mode == HVACMode.OFF

    def test_hvac_action_heating(self, mock_coordinator, thermostat_device):
        """Test HVAC action reading (heating)."""
        thermostat_device.state["thermostatAction"] = "HEATING"
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.hvac_action == HVACAction.HEATING

    def test_hvac_action_cooling(self, mock_coordinator, thermostat_device):
        """Test HVAC action reading (cooling)."""
        thermostat_device.state["thermostatAction"] = "COOLING"
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.hvac_action == HVACAction.COOLING

    def test_hvac_action_idle(self, mock_coordinator, thermostat_device):
        """Test HVAC action reading (idle)."""
        thermostat_device.state["thermostatAction"] = "IDLE"
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.hvac_action == HVACAction.IDLE

    def test_preset_mode_reading(self, mock_coordinator, thermostat_device):
        """Test preset mode reading."""
        thermostat_device.state["preset_mode"] = "comfort"
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.preset_mode == "comfort"

    def test_availability_when_online(self, mock_coordinator, thermostat_device):
        """Test entity is available when online."""
        thermostat_device.online = True
        mock_coordinator.last_update_success = True
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.available is True

    def test_unavailable_when_offline(self, mock_coordinator, thermostat_device):
        """Test entity is unavailable when offline."""
        thermostat_device.online = False
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)

        assert entity.available is False

    def test_device_info(self, mock_coordinator, thermostat_device):
        """Test device registry info."""
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)
        device_info = entity.device_info

        assert device_info["identifiers"] == {("alexa", thermostat_device.id)}
        assert device_info["name"] == "Smart Thermostat (Ecobee)"
        assert device_info["manufacturer"] == "Ecobee"


class TestAlexaClimateCommands:
    """Test climate entity commands."""

    @pytest.mark.asyncio
    async def test_set_temperature(self, mock_coordinator, thermostat_device):
        """Test setting target temperature."""
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)
        await entity.async_set_temperature(temperature=23.0)

        mock_coordinator.api_client.set_temperature.assert_called_once_with(
            thermostat_device.id, 23.0
        )
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_temperature_clamps_min(self, mock_coordinator, thermostat_device):
        """Test temperature clamping (minimum)."""
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)
        await entity.async_set_temperature(temperature=5.0)  # Below min

        # Should clamp to minimum 10.0
        mock_coordinator.api_client.set_temperature.assert_called_once_with(
            thermostat_device.id, 10.0
        )

    @pytest.mark.asyncio
    async def test_set_temperature_clamps_max(self, mock_coordinator, thermostat_device):
        """Test temperature clamping (maximum)."""
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)
        await entity.async_set_temperature(temperature=50.0)  # Above max

        # Should clamp to maximum 38.0
        mock_coordinator.api_client.set_temperature.assert_called_once_with(
            thermostat_device.id, 38.0
        )

    @pytest.mark.asyncio
    async def test_set_hvac_mode_heat(self, mock_coordinator, thermostat_device):
        """Test setting HVAC mode to heat."""
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)
        with patch.object(entity, 'async_write_ha_state'):
            await entity.async_set_hvac_mode(HVACMode.HEAT)

        assert thermostat_device.state["thermostatMode"] == "HEAT"

    @pytest.mark.asyncio
    async def test_set_hvac_mode_cool(self, mock_coordinator, thermostat_device):
        """Test setting HVAC mode to cool."""
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)
        with patch.object(entity, 'async_write_ha_state'):
            await entity.async_set_hvac_mode(HVACMode.COOL)

        assert thermostat_device.state["thermostatMode"] == "COOL"

    @pytest.mark.asyncio
    async def test_set_preset_mode(self, mock_coordinator, thermostat_device):
        """Test setting preset mode."""
        entity = AlexaClimateEntity(mock_coordinator, thermostat_device)
        with patch.object(entity, 'async_write_ha_state'):
            await entity.async_set_preset_mode("eco")

        assert thermostat_device.state["preset_mode"] == "eco"
        mock_coordinator.async_request_refresh.assert_called_once()


class TestClimatePlatformSetup:
    """Test climate platform setup."""

    @pytest.mark.asyncio
    async def test_setup_creates_climate_entity(self, mock_coordinator):
        """Test that setup creates climate entity."""
        hass = AsyncMock(spec=HomeAssistant)
        config_entry = MagicMock()
        config_entry.entry_id = "test-entry"
        async_add_entities = AsyncMock()

        hass.data = {"alexa": {"test-entry": {"coordinator": mock_coordinator}}}

        await async_setup_entry(hass, config_entry, async_add_entities)

        assert async_add_entities.call_count == 1
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], AlexaClimateEntity)
