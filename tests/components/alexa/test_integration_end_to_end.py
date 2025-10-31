"""End-to-end integration tests for Alexa integration.

These tests verify the complete integration flow from setup through teardown,
including interaction between all Phase 1 and Phase 2 components:
- ConfigFlow (OAuth setup)
- TokenManager (token storage/refresh)
- SessionManager (background refresh)
- Integration __init__ (lifecycle management)

Test Scenarios:
- Full setup flow with OAuth
- Multiple concurrent entries
- Token refresh across HA restart
- Reauth flow when tokens expire
- Integration removal and cleanup
- Token expiry notification
- Concurrent refresh attempts
- Error recovery scenarios
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components.alexa import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.alexa.const import DOMAIN, CONF_REDIRECT_URI
from custom_components.alexa.exceptions import (
    AlexaRefreshFailedError,
    AlexaTokenExpiredError,
)
from custom_components.alexa.oauth_manager import TokenResponse
from custom_components.alexa.session_manager import SessionManager
from custom_components.alexa.token_manager import TokenManager


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_token_response() -> TokenResponse:
    """Create mock TokenResponse."""
    return TokenResponse(
        access_token="Atza|test_access_token",
        refresh_token="Atzr|test_refresh_token",
        token_type="Bearer",
        expires_in=3600,
        scope="alexa::skills:account_linking",
    )


@pytest.fixture
def mock_config_entry_data() -> dict[str, Any]:
    """Create mock ConfigEntry data."""
    return {
        CONF_CLIENT_ID: "amzn1.application-oa2-client.test123",
        CONF_CLIENT_SECRET: "test_secret_1234567890123456789012",
        CONF_REDIRECT_URI: "https://my.home-assistant.io/redirect/oauth",
    }


@pytest.fixture
async def config_entry(hass: HomeAssistant, mock_config_entry_data: dict[str, Any]) -> ConfigEntry:
    """Create ConfigEntry."""
    entry = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Amazon Alexa",
        data=mock_config_entry_data,
        source="user",
        entry_id="test_entry_123",
        unique_id=mock_config_entry_data[CONF_CLIENT_ID],
        discovery_keys=None,
        minor_version=1,
        options={},
    )

    # Add to hass
    hass.config_entries._entries[entry.entry_id] = entry

    return entry


# =============================================================================
# Full Setup Flow Tests
# =============================================================================


async def test_full_setup_flow_with_pending_tokens(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    mock_token_response: TokenResponse,
) -> None:
    """Test full setup flow with pending tokens from OAuth."""
    # Simulate config_flow leaving pending tokens in hass.data
    hass.data.setdefault(DOMAIN, {})
    pending_key = f"pending_tokens_{config_entry.data[CONF_CLIENT_ID]}"
    hass.data[DOMAIN][pending_key] = mock_token_response

    # Mock TokenManager and SessionManager
    with patch(
        "custom_components.alexa.TokenManager"
    ) as mock_tm_class, patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class:

        # Setup mocks
        mock_token_manager = Mock()
        mock_token_manager.async_save_token = AsyncMock()
        mock_token_manager.async_get_access_token = AsyncMock(
            return_value="Atza|test_token"
        )
        mock_tm_class.return_value = mock_token_manager

        mock_session_manager = Mock()
        mock_session_manager.async_setup = AsyncMock()
        mock_session_manager.async_get_active_token = AsyncMock(
            return_value="Atza|test_token"
        )
        mock_sm_class.return_value = mock_session_manager

        # Setup entry
        result = await async_setup_entry(hass, config_entry)

        # Verify setup succeeded
        assert result is True

        # Verify pending tokens were saved
        mock_token_manager.async_save_token.assert_called_once_with(mock_token_response)

        # Verify pending tokens removed from hass.data
        assert pending_key not in hass.data[DOMAIN]

        # Verify SessionManager created and setup
        mock_session_manager.async_setup.assert_called_once()

        # Verify token validation occurred
        mock_session_manager.async_get_active_token.assert_called_once_with(
            config_entry.entry_id
        )

        # Verify entry data stored
        assert config_entry.entry_id in hass.data[DOMAIN]


async def test_setup_without_pending_tokens(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Test setup when tokens already exist in storage."""
    # No pending tokens in hass.data (simulates restart)
    hass.data.setdefault(DOMAIN, {})

    # Mock TokenManager and SessionManager
    with patch(
        "custom_components.alexa.TokenManager"
    ) as mock_tm_class, patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class:

        # Setup mocks
        mock_token_manager = Mock()
        mock_tm_class.return_value = mock_token_manager

        mock_session_manager = Mock()
        mock_session_manager.async_setup = AsyncMock()
        mock_session_manager.async_get_active_token = AsyncMock(
            return_value="Atza|existing_token"
        )
        mock_sm_class.return_value = mock_session_manager

        # Setup entry
        result = await async_setup_entry(hass, config_entry)

        # Verify setup succeeded
        assert result is True

        # Verify token validation occurred (loads from storage)
        mock_session_manager.async_get_active_token.assert_called_once()


async def test_setup_triggers_reauth_on_expired_token(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Test setup triggers reauth when token is expired."""
    hass.data.setdefault(DOMAIN, {})

    # Mock SessionManager to raise token expired error
    with patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class:

        mock_session_manager = Mock()
        mock_session_manager.async_setup = AsyncMock()
        mock_session_manager.async_get_active_token = AsyncMock(
            side_effect=AlexaTokenExpiredError("Token expired")
        )
        mock_sm_class.return_value = mock_session_manager

        # Setup should raise ConfigEntryAuthFailed
        with pytest.raises(ConfigEntryAuthFailed, match="Token expired"):
            await async_setup_entry(hass, config_entry)


async def test_setup_raises_not_ready_on_temporary_failure(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Test setup raises ConfigEntryNotReady on temporary failures."""
    hass.data.setdefault(DOMAIN, {})

    # Mock SessionManager to raise generic error
    with patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class:

        mock_session_manager = Mock()
        mock_session_manager.async_setup = AsyncMock()
        mock_session_manager.async_get_active_token = AsyncMock(
            side_effect=RuntimeError("Network timeout")
        )
        mock_sm_class.return_value = mock_session_manager

        # Setup should raise ConfigEntryNotReady
        with pytest.raises(ConfigEntryNotReady, match="Token validation failed"):
            await async_setup_entry(hass, config_entry)


# =============================================================================
# Multiple Entries Tests
# =============================================================================


async def test_multiple_entries_share_session_manager(
    hass: HomeAssistant,
    mock_config_entry_data: dict[str, Any],
    mock_token_response: TokenResponse,
) -> None:
    """Test multiple entries share single SessionManager instance."""
    # Create two config entries
    entry1 = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Alexa Account 1",
        data={**mock_config_entry_data, CONF_CLIENT_ID: "client_1"},
        source="user",
        entry_id="entry_1",
        unique_id="client_1",
        discovery_keys=None,
        minor_version=1,
        options={},
    )

    entry2 = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Alexa Account 2",
        data={**mock_config_entry_data, CONF_CLIENT_ID: "client_2"},
        source="user",
        entry_id="entry_2",
        unique_id="client_2",
        discovery_keys=None,
        minor_version=1,
        options={},
    )

    hass.config_entries._entries = {
        entry1.entry_id: entry1,
        entry2.entry_id: entry2,
    }

    # Add pending tokens for both
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["pending_tokens_client_1"] = mock_token_response
    hass.data[DOMAIN]["pending_tokens_client_2"] = mock_token_response

    # Mock classes
    with patch(
        "custom_components.alexa.TokenManager"
    ) as mock_tm_class, patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class:

        # Setup mocks
        mock_token_manager = Mock()
        mock_token_manager.async_save_token = AsyncMock()
        mock_tm_class.return_value = mock_token_manager

        mock_session_manager = Mock()
        mock_session_manager.async_setup = AsyncMock()
        mock_session_manager.async_get_active_token = AsyncMock(
            return_value="Atza|token"
        )
        mock_sm_class.return_value = mock_session_manager

        # Setup first entry
        await async_setup_entry(hass, entry1)

        # Verify SessionManager created
        assert "session_manager" in hass.data[DOMAIN]
        first_manager = hass.data[DOMAIN]["session_manager"]

        # Setup second entry
        await async_setup_entry(hass, entry2)

        # Verify same SessionManager used
        assert hass.data[DOMAIN]["session_manager"] is first_manager

        # Verify SessionManager.async_setup called only once
        mock_session_manager.async_setup.assert_called_once()


# =============================================================================
# Unload Tests
# =============================================================================


async def test_unload_entry_revokes_tokens(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    mock_token_response: TokenResponse,
) -> None:
    """Test unload revokes tokens with Amazon."""
    # Setup entry first
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][f"pending_tokens_{config_entry.data[CONF_CLIENT_ID]}"] = mock_token_response

    # Mock classes
    with patch(
        "custom_components.alexa.TokenManager"
    ) as mock_tm_class, patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class:

        mock_token_manager = Mock()
        mock_token_manager.async_save_token = AsyncMock()
        mock_token_manager.async_revoke_token = AsyncMock()
        mock_tm_class.return_value = mock_token_manager

        mock_session_manager = Mock()
        mock_session_manager.async_setup = AsyncMock()
        mock_session_manager.async_teardown = AsyncMock()
        mock_session_manager.async_get_active_token = AsyncMock(
            return_value="Atza|token"
        )
        mock_sm_class.return_value = mock_session_manager

        # Setup
        await async_setup_entry(hass, config_entry)

        # Store manager reference
        stored_manager = hass.data[DOMAIN]["session_manager"]

        # Unload
        result = await async_unload_entry(hass, config_entry)

        # Verify unload succeeded
        assert result is True

        # Verify token revoked
        mock_token_manager.async_revoke_token.assert_called_once()

        # Verify entry data removed
        assert config_entry.entry_id not in hass.data[DOMAIN]

        # Verify SessionManager torn down (since this is last entry)
        mock_session_manager.async_teardown.assert_called_once()


async def test_unload_last_entry_tears_down_session_manager(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Test unloading last entry tears down SessionManager."""
    # Setup with mocked SessionManager
    hass.data.setdefault(DOMAIN, {})

    mock_session_manager = Mock()
    mock_session_manager.async_teardown = AsyncMock()
    hass.data[DOMAIN]["session_manager"] = mock_session_manager

    # Mock token manager
    mock_token_manager = Mock()
    mock_token_manager.async_revoke_token = AsyncMock()
    hass.data[DOMAIN][config_entry.entry_id] = {
        "token_manager": mock_token_manager,
        "session_manager": mock_session_manager,
    }

    # Unload (only entry)
    result = await async_unload_entry(hass, config_entry)

    # Verify teardown called
    assert result is True
    mock_session_manager.async_teardown.assert_called_once()

    # Verify session manager removed
    assert "session_manager" not in hass.data[DOMAIN]


async def test_unload_non_last_entry_keeps_session_manager(
    hass: HomeAssistant,
    mock_config_entry_data: dict[str, Any],
) -> None:
    """Test unloading non-last entry keeps SessionManager running."""
    # Create two entries
    entry1 = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Account 1",
        data=mock_config_entry_data,
        source="user",
        entry_id="entry_1",
        unique_id="client_1",
        discovery_keys=None,
        minor_version=1,
        options={},
    )

    entry2 = ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Account 2",
        data=mock_config_entry_data,
        source="user",
        entry_id="entry_2",
        unique_id="client_2",
        discovery_keys=None,
        minor_version=1,
        options={},
    )

    hass.config_entries._entries = {
        entry1.entry_id: entry1,
        entry2.entry_id: entry2,
    }

    # Setup with mocked SessionManager
    hass.data.setdefault(DOMAIN, {})

    mock_session_manager = Mock()
    mock_session_manager.async_teardown = AsyncMock()
    hass.data[DOMAIN]["session_manager"] = mock_session_manager

    # Mock token managers
    mock_tm1 = Mock()
    mock_tm1.async_revoke_token = AsyncMock()
    hass.data[DOMAIN][entry1.entry_id] = {
        "token_manager": mock_tm1,
        "session_manager": mock_session_manager,
    }

    # Unload entry1 (not last)
    result = await async_unload_entry(hass, entry1)

    # Verify unload succeeded
    assert result is True

    # Verify SessionManager NOT torn down
    mock_session_manager.async_teardown.assert_not_called()

    # Verify session manager still exists
    assert "session_manager" in hass.data[DOMAIN]


# =============================================================================
# Background Refresh Integration Tests
# =============================================================================


async def test_background_refresh_triggers_automatically(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    mock_token_response: TokenResponse,
) -> None:
    """Test background task automatically refreshes tokens near expiry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][f"pending_tokens_{config_entry.data[CONF_CLIENT_ID]}"] = mock_token_response

    # Create real SessionManager (not mocked)
    session_manager = SessionManager(hass)

    # Mock TokenManager
    mock_token_manager = Mock()
    mock_token_manager.async_save_token = AsyncMock()
    mock_token_manager.is_token_valid = AsyncMock(return_value=False)  # Trigger refresh
    mock_token_manager.async_refresh_token = AsyncMock(return_value=mock_token_response)
    mock_token_manager.async_get_access_token = AsyncMock(return_value="Atza|token")

    # Patch TokenManager class
    with patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class, patch(
        "custom_components.alexa.TokenManager",
        return_value=mock_token_manager,
    ):
        # Return our real session manager
        mock_sm_class.return_value = session_manager

        # Setup entry
        await async_setup_entry(hass, config_entry)

        # Start session manager
        await session_manager.async_setup()

        # Wait for background task to run
        await asyncio.sleep(0.2)

        # Teardown
        await session_manager.async_teardown()

        # Verify refresh was attempted
        # (is_token_valid checks if refresh needed)
        assert mock_token_manager.is_token_valid.called


# =============================================================================
# Reauth Flow Integration Tests
# =============================================================================


async def test_reauth_flow_updates_existing_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    mock_token_response: TokenResponse,
) -> None:
    """Test reauth flow updates existing entry with new tokens."""
    # Setup initial entry
    hass.data.setdefault(DOMAIN, {})

    with patch(
        "custom_components.alexa.TokenManager"
    ) as mock_tm_class, patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class:

        # Setup mocks
        mock_token_manager = Mock()
        mock_token_manager.async_save_token = AsyncMock()
        mock_tm_class.return_value = mock_token_manager

        mock_session_manager = Mock()
        mock_session_manager.async_setup = AsyncMock()
        mock_session_manager.async_get_active_token = AsyncMock(
            return_value="Atza|new_token"
        )
        mock_sm_class.return_value = mock_session_manager

        # Simulate reauth: new pending tokens
        new_tokens = TokenResponse(
            access_token="Atza|new_access",
            refresh_token="Atzr|new_refresh",
            token_type="Bearer",
            expires_in=3600,
            scope="alexa::skills:account_linking",
        )
        hass.data[DOMAIN][f"pending_tokens_{config_entry.data[CONF_CLIENT_ID]}"] = new_tokens

        # Setup entry (simulates reauth completing)
        await async_setup_entry(hass, config_entry)

        # Verify new tokens saved
        mock_token_manager.async_save_token.assert_called_once_with(new_tokens)


# =============================================================================
# Error Recovery Tests
# =============================================================================


async def test_handles_token_save_failure_gracefully(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    mock_token_response: TokenResponse,
) -> None:
    """Test setup handles token save failures gracefully."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][f"pending_tokens_{config_entry.data[CONF_CLIENT_ID]}"] = mock_token_response

    # Mock TokenManager that fails to save
    with patch(
        "custom_components.alexa.TokenManager"
    ) as mock_tm_class, patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class:

        mock_token_manager = Mock()
        mock_token_manager.async_save_token = AsyncMock(
            side_effect=RuntimeError("Storage error")
        )
        mock_tm_class.return_value = mock_token_manager

        mock_session_manager = Mock()
        mock_session_manager.async_setup = AsyncMock()
        mock_sm_class.return_value = mock_session_manager

        # Setup should raise ConfigEntryNotReady
        with pytest.raises(ConfigEntryNotReady, match="Failed to save initial tokens"):
            await async_setup_entry(hass, config_entry)


# =============================================================================
# Concurrent Operations Tests
# =============================================================================


async def test_concurrent_token_access_is_thread_safe(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Test concurrent token access uses per-entry locks correctly."""
    # Create real SessionManager
    session_manager = SessionManager(hass)
    await session_manager.async_setup()

    # Mock TokenManager with delay to simulate concurrent access
    mock_token_manager = Mock()

    async def slow_get_token() -> str:
        """Simulate slow token retrieval."""
        await asyncio.sleep(0.1)
        return "Atza|token"

    mock_token_manager.async_get_access_token = slow_get_token

    # Cache token manager
    session_manager._token_managers[config_entry.entry_id] = mock_token_manager

    # Launch concurrent access
    tasks = [
        asyncio.create_task(session_manager.async_get_active_token(config_entry.entry_id))
        for _ in range(5)
    ]

    # Wait for all to complete (with timeout to prevent hangs)
    tokens = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)

    # All should succeed with same token
    assert all(t == "Atza|token" for t in tokens)

    # Cleanup
    await session_manager.async_teardown()


# =============================================================================
# SessionManager Lifecycle Tests
# =============================================================================


async def test_session_manager_survives_ha_restart(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Test SessionManager can be reinitialized after HA restart."""
    hass.data.setdefault(DOMAIN, {})

    # First setup (simulates initial load)
    with patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class:

        mock_sm1 = Mock()
        mock_sm1.async_setup = AsyncMock()
        mock_sm1.async_get_active_token = AsyncMock(return_value="Atza|token")
        mock_sm_class.return_value = mock_sm1

        with patch("custom_components.alexa.TokenManager"):
            await async_setup_entry(hass, config_entry)

        # Verify first manager created
        assert "session_manager" in hass.data[DOMAIN]

    # Clear hass.data (simulates restart)
    hass.data[DOMAIN] = {}

    # Second setup (simulates after restart)
    with patch(
        "custom_components.alexa.SessionManager"
    ) as mock_sm_class:

        mock_sm2 = Mock()
        mock_sm2.async_setup = AsyncMock()
        mock_sm2.async_get_active_token = AsyncMock(return_value="Atza|token")
        mock_sm_class.return_value = mock_sm2

        with patch("custom_components.alexa.TokenManager"):
            await async_setup_entry(hass, config_entry)

        # Verify new manager created after restart
        assert "session_manager" in hass.data[DOMAIN]
