"""Fixtures for Alexa integration tests."""

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.fixture
def hass():
    """Fixture to provide a test instance of Home Assistant."""
    # This is a simplified fixture - in real HA tests, use pytest_homeassistant_custom_component
    import asyncio
    from homeassistant.core import HomeAssistant

    # Get the current event loop
    loop = asyncio.get_event_loop()

    hass = HomeAssistant()
    hass.loop = loop
    # Pre-configure required attributes before spec checking
    hass.data = {}
    hass.config = type('Config', (), {})()
    hass.config.path = lambda x: f"/config/{x}"

    yield hass

    # Cleanup
    loop.run_until_complete(hass.async_stop())
