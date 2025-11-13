"""Data models for Alexa OAuth2 integration.

This module defines the data structures used throughout the Alexa integration:
- AlexaCapability: Represents a device capability (power control, brightness, etc.)
- AlexaDevice: Represents an Alexa device with capabilities and state
- DeviceState: Enum for common device states

These models parse API responses and provide convenient methods for checking
device capabilities and accessing device state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeviceState(str, Enum):
    """Common device state values."""

    ON = "ON"
    OFF = "OFF"


class AlexaInterface(str, Enum):
    """Alexa capability interface names.

    These represent the different capabilities a device can have.
    See: https://developer.amazon.com/en-US/docs/alexa/device-apis/list-of-interfaces.html
    """

    POWER_CONTROLLER = "Alexa.PowerController"
    BRIGHTNESS_CONTROLLER = "Alexa.BrightnessController"
    COLOR_CONTROLLER = "Alexa.ColorController"
    COLOR_TEMPERATURE_CONTROLLER = "Alexa.ColorTemperatureController"
    THERMOSTAT_CONTROLLER = "Alexa.ThermostatController"
    LOCK_CONTROLLER = "Alexa.LockController"
    CAMERA = "Alexa.Camera"
    CONTACT_SENSOR = "Alexa.ContactSensor"
    TEMPERATURE_SENSOR = "Alexa.TemperatureSensor"
    MOTION_SENSOR = "Alexa.MotionSensor"


@dataclass
class AlexaCapability:
    """Represents a single Alexa device capability.

    A capability describes what a device can do (e.g., turn on/off, control brightness).

    Attributes:
        interface: The capability interface name (e.g., "Alexa.PowerController")
        version: The interface version (e.g., "3")
        properties: Optional properties of the capability (device-specific)
    """

    interface: str
    version: str
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> AlexaCapability:
        """Parse capability from Alexa API response.

        Args:
            data: Dictionary with keys 'interface', 'version', optional 'properties'

        Returns:
            AlexaCapability instance
        """
        return cls(
            interface=data.get("interface", ""),
            version=data.get("version", ""),
            properties=data.get("properties", {}),
        )

    def matches_interface(self, interface: str | AlexaInterface) -> bool:
        """Check if this capability matches the given interface.

        Args:
            interface: Interface name or AlexaInterface enum value

        Returns:
            True if interface matches
        """
        interface_str = interface.value if isinstance(interface, AlexaInterface) else interface
        return self.interface == interface_str


@dataclass
class AlexaDevice:
    """Represents an Alexa device (light, switch, thermostat, etc.).

    Attributes:
        id: Unique Alexa device ID
        name: User-friendly device name
        device_type: Device category (LIGHT, SWITCH, THERMOSTAT, etc.)
        online: Whether device is currently online
        capabilities: List of device capabilities
        manufacturer_name: Device manufacturer name
        model_name: Device model name
        state: Current device state (powerState, brightness, etc.)
    """

    id: str
    name: str
    device_type: str
    online: bool
    capabilities: list[AlexaCapability] = field(default_factory=list)
    manufacturer_name: str | None = None
    model_name: str | None = None
    state: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> AlexaDevice:
        """Parse device from Alexa API response.

        Handles the full device object returned by /v1/devices API endpoint.

        Args:
            data: Device object from Alexa API response

        Returns:
            AlexaDevice instance

        Example:
            >>> api_data = {
            ...     "id": "amzn1.alexa.device.test123",
            ...     "name": "Living Room Light",
            ...     "deviceType": "LIGHT",
            ...     "online": True,
            ...     "capabilities": [
            ...         {"interface": "Alexa.PowerController", "version": "3"}
            ...     ]
            ... }
            >>> device = AlexaDevice.from_api_response(api_data)
            >>> device.name
            'Living Room Light'
        """
        # Parse capabilities from API response
        capabilities = []
        for cap_data in data.get("capabilities", []):
            capabilities.append(AlexaCapability.from_api_response(cap_data))

        return cls(
            id=data.get("id", ""),
            name=data.get("name", "Unknown"),
            device_type=data.get("deviceType", ""),
            online=data.get("online", False),
            capabilities=capabilities,
            manufacturer_name=data.get("manufacturerName"),
            model_name=data.get("modelName"),
            state={},  # State is updated separately via update_state()
        )

    def supports_capability(self, interface: str | AlexaInterface) -> bool:
        """Check if device has a specific capability.

        Args:
            interface: Capability interface to check (name or AlexaInterface enum)

        Returns:
            True if device supports this capability

        Example:
            >>> device.supports_capability(AlexaInterface.POWER_CONTROLLER)
            True
            >>> device.supports_capability("Alexa.BrightnessController")
            True
        """
        return any(cap.matches_interface(interface) for cap in self.capabilities)

    def get_capability(self, interface: str | AlexaInterface) -> AlexaCapability | None:
        """Get capability object by interface name.

        Args:
            interface: Capability interface to retrieve

        Returns:
            AlexaCapability if found, None otherwise
        """
        interface_str = interface.value if isinstance(interface, AlexaInterface) else interface
        for cap in self.capabilities:
            if cap.interface == interface_str:
                return cap
        return None

    def get_power_state(self) -> bool:
        """Get current power state of device.

        Returns:
            True if device is ON, False if OFF or state not available
        """
        power_state = self.state.get("powerState", "").upper()
        return power_state == "ON"

    def update_state(self, new_state: dict[str, Any]) -> None:
        """Update device state from API response.

        Args:
            new_state: Dictionary with new state values
        """
        self.state.update(new_state)

    @property
    def unique_id(self) -> str:
        """Generate unique identifier for Home Assistant entity registry.

        Returns:
            Unique ID (e.g., "alexa_amzn1.alexa.device.test123")
        """
        return f"alexa_{self.id}"

    @property
    def is_controllable(self) -> bool:
        """Check if device has any controllable capabilities.

        Returns:
            True if device can be controlled
        """
        controllable_interfaces = [
            AlexaInterface.POWER_CONTROLLER,
            AlexaInterface.BRIGHTNESS_CONTROLLER,
            AlexaInterface.COLOR_CONTROLLER,
            AlexaInterface.COLOR_TEMPERATURE_CONTROLLER,
            AlexaInterface.THERMOSTAT_CONTROLLER,
            AlexaInterface.LOCK_CONTROLLER,
        ]
        return any(self.supports_capability(iface) for iface in controllable_interfaces)

    @property
    def display_name(self) -> str:
        """Get display name for UI.

        Formats name with manufacturer if available.

        Returns:
            Display name (e.g., "Living Room Light (Philips)")
        """
        if self.manufacturer_name:
            return f"{self.name} ({self.manufacturer_name})"
        return self.name

    def __repr__(self) -> str:
        """String representation for debugging."""
        status = "online" if self.online else "offline"
        return f"AlexaDevice(id={self.id!r}, name={self.name!r}, type={self.device_type!r}, status={status})"
