"""Fixtures for Alexa integration tests."""

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


@pytest.fixture
def hass(event_loop):
    """Fixture to provide a test instance of Home Assistant."""
    # This is a simplified fixture - in real HA tests, use pytest_homeassistant_custom_component
    from homeassistant.core import HomeAssistant

    hass = HomeAssistant()
    hass.loop = event_loop

    yield hass

    # Cleanup
    event_loop.run_until_complete(hass.async_stop())
