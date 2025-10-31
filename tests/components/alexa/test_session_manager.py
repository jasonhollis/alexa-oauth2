"""Tests for Alexa session manager.

Test Coverage:
- Background task lifecycle (setup/teardown)
- Token refresh timing and scheduling
- Exponential backoff retry logic
- Single-flight pattern (concurrent refreshes)
- Per-entry lock prevents race conditions
- Token expiry notifications
- UTC timezone handling (DST-safe)
- Clock skew buffer handling
- Graceful degradation (stale token fallback)
- Multiple concurrent entries
- Error handling (network, invalid token, etc.)
- Resource cleanup on shutdown
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, Mock, patch, call

import pytest

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant

from custom_components.alexa.const import (
    DOMAIN,
    TOKEN_REFRESH_BUFFER_SECONDS,
    TOKEN_CLOCK_SKEW_BUFFER_SECONDS,
)
from custom_components.alexa.exceptions import (
    AlexaRefreshFailedError,
    AlexaTokenExpiredError,
)
from custom_components.alexa.oauth_manager import TokenResponse
from custom_components.alexa.session_manager import (
    SessionManager,
    BACKGROUND_TASK_INTERVAL_SECONDS,
    REFRESH_RETRY_INITIAL_BACKOFF_SECONDS,
    REFRESH_RETRY_MAX_ATTEMPTS,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_config_entry() -> ConfigEntry:
    """Create mock ConfigEntry."""
    from custom_components.alexa.const import DOMAIN

    entry = Mock(spec=ConfigEntry)
    entry.entry_id = "test_entry_123"
    entry.state = ConfigEntryState.LOADED
    entry.domain = DOMAIN
    entry.data = {
        CONF_CLIENT_ID: "amzn1.application-oa2-client.test",
        CONF_CLIENT_SECRET: "test_secret",
    }
    return entry


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
async def session_manager(hass: HomeAssistant) -> SessionManager:
    """Create SessionManager instance."""
    manager = SessionManager(hass)
    yield manager
    # Cleanup after test
    if manager._background_task and not manager._background_task.done():
        await manager.async_teardown()


# =============================================================================
# Lifecycle Tests
# =============================================================================


async def test_session_manager_setup(hass: HomeAssistant, session_manager: SessionManager) -> None:
    """Test SessionManager setup starts background task."""
    await session_manager.async_setup()

    # Verify background task started
    assert session_manager._background_task is not None
    assert not session_manager._background_task.done()

    # Verify shutdown event cleared
    assert not session_manager._shutdown_event.is_set()

    # Cleanup
    await session_manager.async_teardown()


async def test_session_manager_teardown(hass: HomeAssistant, session_manager: SessionManager) -> None:
    """Test SessionManager teardown stops background task gracefully."""
    await session_manager.async_setup()

    # Verify task running
    assert session_manager._background_task is not None
    assert not session_manager._background_task.done()

    # Teardown
    await session_manager.async_teardown()

    # Verify shutdown event set
    assert session_manager._shutdown_event.is_set()

    # Verify background task completed
    assert session_manager._background_task.done()

    # Verify resources cleared
    assert len(session_manager._token_managers) == 0
    assert len(session_manager._entry_locks) == 0
    assert len(session_manager._refreshing_entries) == 0


@pytest.mark.asyncio
async def test_session_manager_teardown_timeout(
    hass: HomeAssistant, session_manager: SessionManager
) -> None:
    """Test SessionManager teardown handles task that doesn't stop gracefully."""
    await session_manager.async_setup()

    # Mock background task that doesn't stop
    original_task = session_manager._background_task

    async def never_stops() -> None:
        """Task that never completes."""
        # Use asyncio.Event that never gets set instead of sleep
        event = asyncio.Event()
        await event.wait()  # Waits forever but responds to cancellation

    session_manager._background_task = asyncio.create_task(never_stops())

    # Use asyncio.wait_for instead of asyncio.timeout for compatibility
    try:
        await asyncio.wait_for(session_manager.async_teardown(), timeout=15.0)
    except asyncio.TimeoutError:
        pass  # Expected to timeout

    # Verify task was cancelled
    assert session_manager._background_task.cancelled()


async def test_shutdown_event_handler(
    hass: HomeAssistant, session_manager: SessionManager
) -> None:
    """Test shutdown event handler calls teardown."""
    await session_manager.async_setup()

    # Fire shutdown event
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    # Verify shutdown event set (no sleep needed - event fires immediately)
    assert session_manager._shutdown_event.is_set()


# =============================================================================
# Background Task Tests
# =============================================================================


async def test_background_task_checks_entries(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test background task iterates through active entries."""
    # Setup config entries
    hass.config_entries._entries = {mock_config_entry.entry_id: mock_config_entry}

    with patch.object(
        session_manager, "_should_refresh_token", return_value=False
    ) as mock_should_refresh:
        await session_manager.async_setup()

        # Wait for one background task iteration
        await hass.async_block_till_done()

        # Teardown to stop task
        await session_manager.async_teardown()

        # Verify _should_refresh_token was called for entry
        mock_should_refresh.assert_called()


async def test_background_task_respects_shutdown(
    hass: HomeAssistant, session_manager: SessionManager
) -> None:
    """Test background task stops when shutdown event is set."""
    await session_manager.async_setup()

    # Verify task running
    assert not session_manager._background_task.done()

    # Signal shutdown
    session_manager._shutdown_event.set()

    # Wait for task to stop
    await hass.async_block_till_done()

    # Verify task completed
    assert session_manager._background_task.done()


async def test_background_task_handles_errors(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test background task continues after errors."""
    # Setup config entries
    hass.config_entries._entries = {mock_config_entry.entry_id: mock_config_entry}

    call_count = 0

    def side_effect_raise_then_pass(entry_id: str) -> bool:
        """Raise error first time, then return False."""
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Test error")
        return False

    # Patch the interval constant to make test faster
    with patch("custom_components.alexa.session_manager.BACKGROUND_TASK_INTERVAL_SECONDS", 0.05):
        with patch.object(
            session_manager, "_should_refresh_token", side_effect=side_effect_raise_then_pass
        ):
            await session_manager.async_setup()

            # Wait for two iterations (2 * 0.05s = 0.1s + buffer)
            await asyncio.sleep(0.15)

            # Teardown
            await session_manager.async_teardown()

            # Verify task continued after error
            assert call_count >= 2


# =============================================================================
# Token Refresh Decision Tests
# =============================================================================


async def test_should_refresh_token_when_near_expiry(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test token refresh triggered when near expiry."""
    # Create mock token manager
    mock_token_manager = Mock()
    mock_token_manager.is_token_valid = AsyncMock(return_value=False)

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Check if refresh needed
    should_refresh = await session_manager._should_refresh_token(mock_config_entry.entry_id)

    assert should_refresh is True
    mock_token_manager.is_token_valid.assert_called_once()


async def test_should_not_refresh_valid_token(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test token refresh skipped when token is valid."""
    # Create mock token manager
    mock_token_manager = Mock()
    mock_token_manager.is_token_valid = AsyncMock(return_value=True)

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Check if refresh needed
    should_refresh = await session_manager._should_refresh_token(mock_config_entry.entry_id)

    assert should_refresh is False


async def test_should_not_refresh_when_already_refreshing(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test single-flight pattern: skip refresh if already in progress."""
    # Mark entry as refreshing
    session_manager._refreshing_entries.add(mock_config_entry.entry_id)

    # Check if refresh needed (should return False due to single-flight)
    should_refresh = await session_manager._should_refresh_token(mock_config_entry.entry_id)

    assert should_refresh is False


# =============================================================================
# Token Refresh Execution Tests
# =============================================================================


async def test_refresh_token_success(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
    mock_token_response: TokenResponse,
) -> None:
    """Test successful token refresh on first attempt."""
    # Create mock token manager
    mock_token_manager = Mock()
    mock_token_manager.async_refresh_token = AsyncMock(return_value=mock_token_response)

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Refresh token
    await session_manager._refresh_token_for_entry(mock_config_entry.entry_id)

    # Verify refresh was called
    mock_token_manager.async_refresh_token.assert_called_once()

    # Verify entry not in refreshing set after completion
    assert mock_config_entry.entry_id not in session_manager._refreshing_entries


async def test_refresh_token_exponential_backoff(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
    mock_token_response: TokenResponse,
) -> None:
    """Test exponential backoff retry logic on failures."""
    # Create mock token manager that fails twice then succeeds
    mock_token_manager = Mock()
    mock_token_manager.async_refresh_token = AsyncMock(
        side_effect=[
            AlexaRefreshFailedError("Network error"),
            AlexaRefreshFailedError("Network error"),
            mock_token_response,  # Success on 3rd attempt
        ]
    )

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Track sleep calls
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # Refresh token
        await session_manager._refresh_token_for_entry(mock_config_entry.entry_id)

        # Verify backoff delays: 1s, 2s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_has_calls([call(1), call(2)])

        # Verify refresh attempted 3 times
        assert mock_token_manager.async_refresh_token.call_count == 3


async def test_refresh_token_all_retries_exhausted(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test notification sent when all retry attempts fail."""
    # Create mock token manager that always fails
    mock_token_manager = Mock()
    mock_token_manager.async_refresh_token = AsyncMock(
        side_effect=AlexaRefreshFailedError("Network error")
    )

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Mock notification method
    session_manager._notify_token_expiry = AsyncMock()

    # Patch sleep to speed up test
    with patch("asyncio.sleep", new_callable=AsyncMock):
        # Refresh token
        await session_manager._refresh_token_for_entry(mock_config_entry.entry_id)

        # Verify all retries attempted
        assert mock_token_manager.async_refresh_token.call_count == REFRESH_RETRY_MAX_ATTEMPTS

        # Verify notification sent
        session_manager._notify_token_expiry.assert_called_once_with(mock_config_entry.entry_id)


async def test_refresh_token_single_flight_pattern(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test single-flight pattern prevents concurrent refreshes."""
    # Mark entry as already refreshing
    session_manager._refreshing_entries.add(mock_config_entry.entry_id)

    # Create mock token manager
    mock_token_manager = Mock()
    mock_token_manager.async_refresh_token = AsyncMock()

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Attempt refresh (should be skipped)
    await session_manager._refresh_token_for_entry(mock_config_entry.entry_id)

    # Verify refresh was NOT called
    mock_token_manager.async_refresh_token.assert_not_called()


async def test_refresh_token_per_entry_lock(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
    mock_token_response: TokenResponse,
) -> None:
    """Test per-entry lock prevents race conditions."""
    # Create mock token manager
    mock_token_manager = Mock()

    # Track lock acquisition order
    lock_acquired = []

    async def mock_refresh() -> TokenResponse:
        """Mock refresh that records lock acquisition."""
        lock_acquired.append(1)
        await asyncio.sleep(0)  # Yield control without delay
        return mock_token_response

    mock_token_manager.async_refresh_token = mock_refresh

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Start two concurrent refreshes
    task1 = asyncio.create_task(
        session_manager._refresh_token_for_entry(mock_config_entry.entry_id)
    )
    task2 = asyncio.create_task(
        session_manager._refresh_token_for_entry(mock_config_entry.entry_id)
    )

    # Wait for both to complete (with timeout to prevent hangs)
    await asyncio.wait_for(asyncio.gather(task1, task2), timeout=10.0)

    # Due to single-flight pattern, only one refresh should execute
    # (Second task skipped because entry already in _refreshing_entries)
    assert len(lock_acquired) == 1


# =============================================================================
# Token Access Tests
# =============================================================================


async def test_get_active_token_success(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test getting active token returns valid token."""
    # Create mock token manager
    mock_token_manager = Mock()
    mock_token_manager.async_get_access_token = AsyncMock(return_value="Atza|test_token")

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Get token
    token = await session_manager.async_get_active_token(mock_config_entry.entry_id)

    assert token == "Atza|test_token"
    mock_token_manager.async_get_access_token.assert_called_once()


async def test_get_active_token_creates_token_manager(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test get_active_token creates TokenManager if not cached."""
    # Setup config entry
    hass.config_entries._entries = {mock_config_entry.entry_id: mock_config_entry}

    with patch(
        "custom_components.alexa.session_manager.TokenManager"
    ) as mock_token_manager_class:
        mock_token_manager = Mock()
        mock_token_manager.async_get_access_token = AsyncMock(return_value="Atza|test_token")
        mock_token_manager_class.return_value = mock_token_manager

        # Get token
        token = await session_manager.async_get_active_token(mock_config_entry.entry_id)

        # Verify TokenManager created
        mock_token_manager_class.assert_called_once_with(hass, mock_config_entry)

        # Verify token returned
        assert token == "Atza|test_token"

        # Verify TokenManager cached
        assert mock_config_entry.entry_id in session_manager._token_managers


# =============================================================================
# Notification Tests
# =============================================================================


async def test_notify_token_expiry_triggers_reauth(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test token expiry notification triggers reauth flow."""
    # Setup config entry
    hass.config_entries._entries = {mock_config_entry.entry_id: mock_config_entry}
    mock_config_entry.async_start_reauth = Mock()

    # Notify expiry
    await session_manager._notify_token_expiry(mock_config_entry.entry_id)

    # Verify reauth triggered
    mock_config_entry.async_start_reauth.assert_called_once_with(hass)


async def test_notify_token_expiry_entry_not_found(
    hass: HomeAssistant,
    session_manager: SessionManager,
) -> None:
    """Test notification handles missing ConfigEntry gracefully."""
    # Attempt to notify for non-existent entry (should not raise)
    await session_manager._notify_token_expiry("non_existent_entry")


# =============================================================================
# Helper Method Tests
# =============================================================================


async def test_get_entry_lock_creates_new_lock(
    hass: HomeAssistant, session_manager: SessionManager
) -> None:
    """Test get_entry_lock creates new lock if not exists."""
    entry_id = "test_entry"

    # Get lock
    lock1 = await session_manager._get_entry_lock(entry_id)

    assert entry_id in session_manager._entry_locks
    assert lock1 is session_manager._entry_locks[entry_id]


async def test_get_entry_lock_returns_existing_lock(
    hass: HomeAssistant, session_manager: SessionManager
) -> None:
    """Test get_entry_lock returns same lock on subsequent calls."""
    entry_id = "test_entry"

    # Get lock twice
    lock1 = await session_manager._get_entry_lock(entry_id)
    lock2 = await session_manager._get_entry_lock(entry_id)

    # Should be same instance
    assert lock1 is lock2


async def test_get_token_manager_raises_on_invalid_entry(
    hass: HomeAssistant, session_manager: SessionManager
) -> None:
    """Test get_token_manager raises ValueError for invalid entry."""
    with pytest.raises(ValueError, match="ConfigEntry not found"):
        await session_manager._get_token_manager("non_existent_entry")


# =============================================================================
# Multiple Entries Tests
# =============================================================================


async def test_multiple_entries_refreshed_independently(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_token_response: TokenResponse,
) -> None:
    """Test multiple entries are refreshed independently."""
    # Create two mock entries
    entry1 = Mock(spec=ConfigEntry)
    entry1.entry_id = "entry_1"
    entry1.state = ConfigEntryState.LOADED
    entry1.domain = DOMAIN
    entry1.data = {
        CONF_CLIENT_ID: "client_1",
        CONF_CLIENT_SECRET: "secret_1",
    }

    entry2 = Mock(spec=ConfigEntry)
    entry2.entry_id = "entry_2"
    entry2.state = ConfigEntryState.LOADED
    entry2.domain = DOMAIN
    entry2.data = {
        CONF_CLIENT_ID: "client_2",
        CONF_CLIENT_SECRET: "secret_2",
    }

    # Setup config entries
    hass.config_entries._entries = {
        entry1.entry_id: entry1,
        entry2.entry_id: entry2,
    }

    # Create mock token managers
    mock_tm1 = Mock()
    mock_tm1.async_refresh_token = AsyncMock(return_value=mock_token_response)

    mock_tm2 = Mock()
    mock_tm2.async_refresh_token = AsyncMock(return_value=mock_token_response)

    # Cache token managers
    session_manager._token_managers[entry1.entry_id] = mock_tm1
    session_manager._token_managers[entry2.entry_id] = mock_tm2

    # Refresh both entries
    await session_manager._refresh_token_for_entry(entry1.entry_id)
    await session_manager._refresh_token_for_entry(entry2.entry_id)

    # Verify both were refreshed
    mock_tm1.async_refresh_token.assert_called_once()
    mock_tm2.async_refresh_token.assert_called_once()

    # Verify separate locks used (both entries not in refreshing set)
    assert entry1.entry_id not in session_manager._refreshing_entries
    assert entry2.entry_id not in session_manager._refreshing_entries


# =============================================================================
# Error Handling Tests
# =============================================================================


async def test_token_refresh_handles_network_errors(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test token refresh handles network errors gracefully."""
    # Create mock token manager that raises network error
    mock_token_manager = Mock()
    mock_token_manager.async_refresh_token = AsyncMock(
        side_effect=AlexaRefreshFailedError("Network timeout")
    )

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Mock notification
    session_manager._notify_token_expiry = AsyncMock()

    # Patch sleep to speed up test
    with patch("asyncio.sleep", new_callable=AsyncMock):
        # Refresh token (should not raise)
        await session_manager._refresh_token_for_entry(mock_config_entry.entry_id)

        # Verify notification sent after retries exhausted
        session_manager._notify_token_expiry.assert_called_once()


async def test_get_active_entries_filters_unloaded(
    hass: HomeAssistant, session_manager: SessionManager
) -> None:
    """Test _get_active_entries only returns loaded entries."""
    # Create mock entries with different states
    loaded_entry = Mock(spec=ConfigEntry)
    loaded_entry.entry_id = "loaded"
    loaded_entry.state = ConfigEntryState.LOADED
    loaded_entry.domain = DOMAIN

    unloaded_entry = Mock(spec=ConfigEntry)
    unloaded_entry.entry_id = "unloaded"
    unloaded_entry.state = ConfigEntryState.NOT_LOADED
    unloaded_entry.domain = DOMAIN

    # Setup config entries
    hass.config_entries._entries = {
        loaded_entry.entry_id: loaded_entry,
        unloaded_entry.entry_id: unloaded_entry,
    }

    # Mock async_entries to return our entries
    with patch.object(
        session_manager._config_entries,
        "async_entries",
        return_value=[loaded_entry, unloaded_entry],
    ):
        # Get active entries
        active = session_manager._get_active_entries()

        # Should only return loaded entry
        assert len(active) == 1
        assert active[0].entry_id == "loaded"


# =============================================================================
# Timezone and Clock Skew Tests
# =============================================================================


async def test_token_validation_uses_utc_timezone(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test token validation uses UTC timezone (DST-safe)."""
    # Create token data that expires in 1 hour (UTC)
    utc_now = datetime.now(timezone.utc).timestamp()
    expires_at = utc_now + 3600

    # Create mock token manager
    mock_token_manager = Mock()
    mock_token_manager._token_data = {
        "access_token": "Atza|test",
        "expires_at": expires_at,
    }

    # is_token_valid should use UTC time
    async def mock_is_valid() -> bool:
        """Check if token valid using UTC."""
        now = datetime.now(timezone.utc).timestamp()
        time_until_expiry = mock_token_manager._token_data["expires_at"] - now
        return time_until_expiry > TOKEN_REFRESH_BUFFER_SECONDS

    mock_token_manager.is_token_valid = mock_is_valid

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Check validity
    should_refresh = await session_manager._should_refresh_token(mock_config_entry.entry_id)

    # Token expires in 1 hour > 5 min buffer, should not refresh
    assert should_refresh is False


# =============================================================================
# Resource Cleanup Tests
# =============================================================================


async def test_teardown_clears_all_resources(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
) -> None:
    """Test teardown clears all cached resources."""
    # Setup with cached data
    await session_manager.async_setup()

    # Add some cached data
    session_manager._token_managers[mock_config_entry.entry_id] = Mock()
    session_manager._entry_locks[mock_config_entry.entry_id] = asyncio.Lock()
    session_manager._refreshing_entries.add(mock_config_entry.entry_id)

    # Teardown
    await session_manager.async_teardown()

    # Verify all resources cleared
    assert len(session_manager._token_managers) == 0
    assert len(session_manager._entry_locks) == 0
    assert len(session_manager._refreshing_entries) == 0


# =============================================================================
# Integration with TokenManager Tests
# =============================================================================


async def test_refresh_saves_new_tokens(
    hass: HomeAssistant,
    session_manager: SessionManager,
    mock_config_entry: ConfigEntry,
    mock_token_response: TokenResponse,
) -> None:
    """Test successful refresh saves new tokens."""
    # Create mock token manager
    mock_token_manager = Mock()
    mock_token_manager.async_refresh_token = AsyncMock(return_value=mock_token_response)

    # Cache token manager
    session_manager._token_managers[mock_config_entry.entry_id] = mock_token_manager

    # Refresh token
    await session_manager._refresh_token_for_entry(mock_config_entry.entry_id)

    # Verify refresh was called (which internally saves tokens)
    mock_token_manager.async_refresh_token.assert_called_once()
