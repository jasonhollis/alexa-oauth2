"""Fixtures for Alexa integration tests."""

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.fixture
async def hass(tmp_path):
    """Fixture to provide a test instance of Home Assistant."""
    # This is a simplified fixture - in real HA tests, use pytest_homeassistant_custom_component
    from homeassistant.core import HomeAssistant

    # Create a temporary config directory
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)

    # Create HomeAssistant instance (pytest-asyncio manages the event loop)
    hass = HomeAssistant(config_dir=str(config_dir))
    # Pre-configure required attributes before spec checking
    hass.data = {}
    hass.config = type('Config', (), {})()
    hass.config.path = lambda x: f"{config_dir}/{x}"

    yield hass

    # Cleanup
    await hass.async_stop()
