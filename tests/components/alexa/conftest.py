"""Fixtures for Alexa integration tests."""

import pytest
from unittest.mock import Mock

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
    hass.config.config_dir = str(config_dir)
    hass.config.path = lambda *args: f"{config_dir}/{args[0] if args else ''}"

    # Initialize config_entries with mock manager
    mock_config_entries = Mock()
    mock_config_entries._entries = {}
    mock_config_entries.async_entries = lambda domain=None: (
        [e for e in mock_config_entries._entries.values() if domain is None or e.domain == domain]
    )
    mock_config_entries.async_get_entry = lambda entry_id: mock_config_entries._entries.get(entry_id)
    hass.config_entries = mock_config_entries

    yield hass

    # Cleanup
    await hass.async_stop()
