"""Session management for Alexa integration with automatic token refresh.

This module provides background token refresh and session lifecycle management:
- Background task refreshes tokens before expiry (5-minute buffer)
- Exponential backoff retry logic (1s → 16s, 5 retries)
- Single-flight pattern prevents thundering herd
- Per-entry locks prevent race conditions
- UTC timezone safe (no DST bugs)
- Graceful degradation on failure

Example:
    >>> manager = SessionManager(hass)
    >>> await manager.async_setup()
    >>> # Background task now running
    >>> token = await manager.async_get_active_token(entry_id)
    >>> # Token auto-refreshed if needed
    >>> await manager.async_teardown()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Final

from homeassistant.config_entries import ConfigEntry, ConfigEntries, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, Event

from .const import TOKEN_REFRESH_BUFFER_SECONDS, TOKEN_CLOCK_SKEW_BUFFER_SECONDS
from .exceptions import AlexaRefreshFailedError, AlexaTokenExpiredError
from .token_manager import TokenManager

_LOGGER = logging.getLogger(__name__)

# Background task constants
BACKGROUND_TASK_INTERVAL_SECONDS: Final = 60  # Check every 60 seconds
REFRESH_RETRY_INITIAL_BACKOFF_SECONDS: Final = 1  # Start with 1 second
REFRESH_RETRY_MAX_ATTEMPTS: Final = 5  # Maximum 5 retry attempts
REFRESH_RETRY_MAX_BACKOFF_SECONDS: Final = 16  # Cap backoff at 16 seconds


class SessionManager:
    """Manages Alexa integration sessions with automatic token refresh.

    This class provides:
    - Background task that checks all entries every 60 seconds
    - Automatic token refresh when expiry within 5 minutes
    - Exponential backoff on failures (1s → 2s → 4s → 8s → 16s)
    - Single-flight pattern (one refresh per entry at a time)
    - Per-entry locks for thread safety
    - UTC timezone handling (DST-safe)
    - Token expiry notifications to users
    - Graceful degradation (use stale token on failure)

    Background Task Flow:
        1. Every 60 seconds, check all active ConfigEntries
        2. For each entry, check if token expires within 5 minutes
        3. If yes, refresh token with exponential backoff
        4. If refresh fails after 5 retries, notify user
        5. On shutdown signal, stop background task gracefully

    Example:
        >>> manager = SessionManager(hass)
        >>> await manager.async_setup()
        >>> # Background refresh task now running
        >>>
        >>> # Get valid token (auto-refreshed if needed)
        >>> token = await manager.async_get_active_token("entry_123")
        >>>
        >>> # On shutdown
        >>> await manager.async_teardown()
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize session manager.

        Args:
            hass: Home Assistant instance
        """
        self.hass = hass
        self._config_entries: ConfigEntries = hass.config_entries

        # Background task management
        self._background_task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event = asyncio.Event()

        # Per-entry locks for race condition prevention
        self._entry_locks: dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()

        # Track refreshes in progress (single-flight pattern)
        self._refreshing_entries: set[str] = set()

        # Token managers cache (entry_id -> TokenManager)
        self._token_managers: dict[str, TokenManager] = {}

    # =========================================================================
    # Lifecycle Management
    # =========================================================================

    async def async_setup(self) -> None:
        """Set up session manager and start background refresh task.

        This method:
        1. Initializes shutdown event
        2. Starts background refresh task
        3. Registers shutdown handler

        Must be called once during integration setup.
        """
        _LOGGER.info("Setting up Alexa session manager")

        # Clear shutdown event
        self._shutdown_event.clear()

        # Start background refresh task
        self._background_task = asyncio.create_task(
            self._background_refresh_tokens(),
            name="alexa_session_manager_background_refresh",
        )

        # Register shutdown handler
        self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_handle_shutdown
        )

        _LOGGER.info("Alexa session manager setup complete")

    async def async_teardown(self) -> None:
        """Tear down session manager and stop background task.

        This method:
        1. Signals background task to stop
        2. Waits for task to complete gracefully
        3. Cleans up resources

        Called during integration removal or HA shutdown.
        """
        _LOGGER.info("Tearing down Alexa session manager")

        # Signal shutdown
        self._shutdown_event.set()

        # Wait for background task to complete (with timeout)
        if self._background_task and not self._background_task.done():
            try:
                await asyncio.wait_for(self._background_task, timeout=5.0)
            except asyncio.TimeoutError:
                _LOGGER.warning("Background task did not stop gracefully, cancelling")
                self._background_task.cancel()
                try:
                    await self._background_task
                except asyncio.CancelledError:
                    pass

        # Clear resources
        self._token_managers.clear()
        self._entry_locks.clear()
        self._refreshing_entries.clear()

        _LOGGER.info("Alexa session manager teardown complete")

    async def _async_handle_shutdown(self, event: Event) -> None:
        """Handle Home Assistant shutdown event.

        Args:
            event: Home Assistant shutdown event
        """
        _LOGGER.debug("Received Home Assistant shutdown event")
        await self.async_teardown()

    # =========================================================================
    # Background Refresh Task
    # =========================================================================

    async def _background_refresh_tokens(self) -> None:
        """Background task that refreshes tokens before expiry.

        This task:
        1. Runs every 60 seconds
        2. Checks all active ConfigEntries
        3. Refreshes tokens if expiry within 5 minutes
        4. Uses exponential backoff on failures
        5. Notifies users on persistent failures
        6. Stops gracefully on shutdown signal

        Refresh Strategy:
            - Check token expiry every 60 seconds
            - Refresh if expires within 300 seconds (5 minutes)
            - Use single-flight pattern (one refresh per entry)
            - Exponential backoff: 1s → 2s → 4s → 8s → 16s (5 retries)
            - Notify user if all retries fail
        """
        _LOGGER.info("Background token refresh task started")

        while not self._shutdown_event.is_set():
            try:
                # Get all active Alexa entries
                entries = self._get_active_entries()

                # Check each entry for token refresh needs
                for entry in entries:
                    if self._shutdown_event.is_set():
                        break

                    # Check if refresh needed (non-blocking)
                    if await self._should_refresh_token(entry.entry_id):
                        # Refresh token with exponential backoff
                        await self._refresh_token_for_entry(entry.entry_id)

            except Exception as err:
                _LOGGER.exception("Error in background token refresh: %s", err)

            # Wait 60 seconds for next check (or until shutdown)
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=BACKGROUND_TASK_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                # Timeout is expected (continue loop)
                pass

        _LOGGER.info("Background token refresh task stopped")

    def _get_active_entries(self) -> list[ConfigEntry]:
        """Get all active Alexa ConfigEntries.

        Returns:
            List of active Alexa ConfigEntries
        """
        from .const import DOMAIN

        entries = []
        for entry in self._config_entries.async_entries(DOMAIN):
            # Only include loaded entries (skip disabled or unloaded)
            if entry.state == ConfigEntryState.LOADED:
                entries.append(entry)

        return entries

    async def _should_refresh_token(self, entry_id: str) -> bool:
        """Check if token should be refreshed for entry.

        A token should be refreshed if:
        - Token expires within TOKEN_REFRESH_BUFFER_SECONDS (300s = 5 min)
        - No refresh already in progress (single-flight pattern)

        Args:
            entry_id: ConfigEntry ID to check

        Returns:
            True if refresh needed, False otherwise
        """
        # Skip if refresh already in progress (single-flight)
        if entry_id in self._refreshing_entries:
            return False

        try:
            # Get token manager
            token_manager = await self._get_token_manager(entry_id)

            # Check if token is valid (uses TOKEN_REFRESH_BUFFER_SECONDS internally)
            is_valid = await token_manager.is_token_valid()

            if not is_valid:
                _LOGGER.info(
                    "Token for entry %s needs refresh (expires within %d seconds)",
                    entry_id,
                    TOKEN_REFRESH_BUFFER_SECONDS,
                )
                return True

            return False

        except Exception as err:
            _LOGGER.error("Error checking token validity for %s: %s", entry_id, err)
            return False

    async def _refresh_token_for_entry(self, entry_id: str) -> None:
        """Refresh token for entry with exponential backoff.

        This method:
        1. Uses single-flight pattern (skip if refresh in progress)
        2. Acquires per-entry lock for thread safety
        3. Attempts refresh with exponential backoff (1s → 16s, 5 retries)
        4. Notifies user on persistent failure
        5. Uses graceful degradation (allows stale token on failure)

        Args:
            entry_id: ConfigEntry ID to refresh

        Retry Logic:
            - Attempt 1: Immediate (0s delay)
            - Attempt 2: 1s delay
            - Attempt 3: 2s delay
            - Attempt 4: 4s delay
            - Attempt 5: 8s delay
            - Final delay capped at 16s
        """
        # Single-flight pattern: skip if already refreshing
        if entry_id in self._refreshing_entries:
            _LOGGER.debug("Refresh already in progress for %s, skipping", entry_id)
            return

        # Mark as refreshing
        self._refreshing_entries.add(entry_id)

        try:
            # Get per-entry lock
            lock = await self._get_entry_lock(entry_id)

            async with lock:
                _LOGGER.info("Starting token refresh for entry %s", entry_id)

                # Get token manager
                token_manager = await self._get_token_manager(entry_id)

                # Exponential backoff retry logic
                backoff_seconds = REFRESH_RETRY_INITIAL_BACKOFF_SECONDS

                for attempt in range(1, REFRESH_RETRY_MAX_ATTEMPTS + 1):
                    try:
                        # Attempt token refresh
                        _LOGGER.debug(
                            "Token refresh attempt %d/%d for entry %s",
                            attempt,
                            REFRESH_RETRY_MAX_ATTEMPTS,
                            entry_id,
                        )

                        token_response = await token_manager.async_refresh_token()

                        _LOGGER.info(
                            "Successfully refreshed token for entry %s (attempt %d)",
                            entry_id,
                            attempt,
                        )

                        # Success - exit retry loop
                        return

                    except (AlexaRefreshFailedError, AlexaTokenExpiredError) as err:
                        _LOGGER.warning(
                            "Token refresh failed for entry %s (attempt %d/%d): %s",
                            entry_id,
                            attempt,
                            REFRESH_RETRY_MAX_ATTEMPTS,
                            err,
                        )

                        # If not last attempt, wait with exponential backoff
                        if attempt < REFRESH_RETRY_MAX_ATTEMPTS:
                            await asyncio.sleep(backoff_seconds)
                            # Exponential backoff: 1s → 2s → 4s → 8s → 16s (capped)
                            backoff_seconds = min(
                                backoff_seconds * 2,
                                REFRESH_RETRY_MAX_BACKOFF_SECONDS,
                            )
                        else:
                            # All retries exhausted - notify user
                            _LOGGER.error(
                                "Token refresh failed after %d attempts for entry %s",
                                REFRESH_RETRY_MAX_ATTEMPTS,
                                entry_id,
                            )
                            await self._notify_token_expiry(entry_id)

        finally:
            # Remove from refreshing set
            self._refreshing_entries.discard(entry_id)

    # =========================================================================
    # Token Access
    # =========================================================================

    async def async_get_active_token(self, entry_id: str) -> str:
        """Get valid access token for entry, refreshing if needed.

        This is the main public method for getting tokens. It automatically
        handles refresh if the token is near expiry.

        Args:
            entry_id: ConfigEntry ID

        Returns:
            Valid access token string

        Raises:
            AlexaTokenExpiredError: Token expired and refresh failed

        Example:
            >>> token = await manager.async_get_active_token("entry_123")
            >>> headers = {"Authorization": f"Bearer {token}"}
        """
        # Get token manager
        token_manager = await self._get_token_manager(entry_id)

        # Get access token (auto-refreshes if needed)
        access_token = await token_manager.async_get_access_token()

        return access_token

    # =========================================================================
    # Notification
    # =========================================================================

    async def _notify_token_expiry(self, entry_id: str) -> None:
        """Notify user that token has expired and needs reauth.

        Creates a persistent notification in Home Assistant UI asking the
        user to re-authenticate.

        Args:
            entry_id: ConfigEntry ID with expired token
        """
        # Get ConfigEntry
        entry = self._config_entries.async_get_entry(entry_id)
        if not entry:
            _LOGGER.error("ConfigEntry not found for notification (entry_id=%s)", entry_id)
            return

        _LOGGER.info("Creating token expiry notification for entry %s", entry_id)

        # Fire event for reauth notification
        # Home Assistant will show notification with "Reconfigure" button
        entry.async_start_reauth(self.hass)

    # =========================================================================
    # Helper Methods
    # =========================================================================

    async def _get_entry_lock(self, entry_id: str) -> asyncio.Lock:
        """Get or create per-entry lock for thread safety.

        Args:
            entry_id: ConfigEntry ID

        Returns:
            asyncio.Lock for this entry
        """
        async with self._locks_lock:
            if entry_id not in self._entry_locks:
                self._entry_locks[entry_id] = asyncio.Lock()
            return self._entry_locks[entry_id]

    async def _get_token_manager(self, entry_id: str) -> TokenManager:
        """Get or create TokenManager for entry.

        Args:
            entry_id: ConfigEntry ID

        Returns:
            TokenManager instance for this entry

        Raises:
            ValueError: ConfigEntry not found
        """
        # Check cache first
        if entry_id in self._token_managers:
            return self._token_managers[entry_id]

        # Get ConfigEntry
        entry = self._config_entries.async_get_entry(entry_id)
        if not entry:
            raise ValueError(f"ConfigEntry not found: {entry_id}")

        # Create and cache TokenManager
        token_manager = TokenManager(self.hass, entry)
        self._token_managers[entry_id] = token_manager

        return token_manager
