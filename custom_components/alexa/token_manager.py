"""Token management for Alexa integration.

This module manages OAuth tokens for Amazon Alexa integration with features:
- Encrypted token storage using HA storage API
- Automatic token refresh before expiry
- Token validation and expiry checking
- Token revocation on integration removal

Integration with oauth_manager.py for OAuth operations.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import (
    AMAZON_REVOKE_URL,
    AMAZON_TOKEN_URL,
    CONF_REDIRECT_URI,
    STORAGE_KEY_TOKENS,
    STORAGE_VERSION,
    TOKEN_REFRESH_BUFFER_SECONDS,
    TOKEN_REFRESH_TIMEOUT_SECONDS,
)
from .exceptions import (
    AlexaInvalidGrantError,
    AlexaRefreshFailedError,
    AlexaTokenExpiredError,
)
from .oauth_manager import OAuthManager, TokenResponse

_LOGGER = logging.getLogger(__name__)


class TokenManager:
    """Manages LWA tokens with encryption and refresh.

    This class provides:
    - Encrypted token storage using Home Assistant storage API
    - Automatic token refresh before expiry
    - Token validation and expiry checking
    - Proactive refresh strategy (5 min before expiry)
    - Token revocation for cleanup

    Token Lifecycle:
        1. Tokens received from OAuth flow
        2. Saved to encrypted storage via async_save_token()
        3. Retrieved when needed via async_get_access_token()
        4. Auto-refreshed if near expiry (5 min buffer)
        5. Revoked on integration removal via async_revoke_token()

    Storage Format:
        File: .storage/alexa.{entry_id}.tokens
        Content: {
            "access_token": "Atza|...",
            "refresh_token": "Atzr|...",
            "token_type": "Bearer",
            "expires_at": 1234567890.0,
            "scope": "alexa::skills:account_linking"
        }

    Example:
        >>> manager = TokenManager(hass, entry)
        >>> await manager.async_save_token(token_response)
        >>> access_token = await manager.async_get_access_token()
        >>> # Token auto-refreshes if near expiry
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize token manager.

        Args:
            hass: Home Assistant instance
            entry: ConfigEntry for this integration instance
        """
        self.hass = hass
        self.entry = entry

        # Initialize storage with entry-specific key
        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_TOKENS}.{entry.entry_id}",
        )

        # In-memory cache for token data (loaded from storage)
        self._token_data: dict[str, Any] | None = None

        # Lock for preventing concurrent refresh operations
        self._refresh_lock = asyncio.Lock()

        # OAuth manager for token refresh
        self._oauth_manager: OAuthManager | None = None

    def _get_oauth_manager(self) -> OAuthManager:
        """Get or create OAuth manager instance.

        Returns:
            OAuthManager instance configured with entry credentials
        """
        if self._oauth_manager is None:
            self._oauth_manager = OAuthManager(
                self.hass,
                self.entry.data[CONF_CLIENT_ID],
                self.entry.data[CONF_CLIENT_SECRET],
            )
        return self._oauth_manager

    # =========================================================================
    # Token Retrieval
    # =========================================================================

    async def async_get_access_token(self) -> str:
        """Get valid access token, refreshing if needed.

        This method provides the main interface for getting access tokens.
        It automatically handles token refresh if the token is expired or
        near expiry (within TOKEN_REFRESH_BUFFER_SECONDS).

        Returns:
            Valid access token string

        Raises:
            AlexaTokenExpiredError: Token expired and refresh failed
            AlexaRefreshFailedError: Token refresh request failed

        Flow:
            1. Check if token is valid (not expired)
            2. If invalid, refresh token
            3. Return access_token from storage

        Refresh Strategy:
            - Refresh if expires_at - now < TOKEN_REFRESH_BUFFER_SECONDS
            - Buffer is 300 seconds (5 minutes) by default
            - Prevents tokens expiring during use

        Example:
            >>> try:
            ...     access_token = await manager.async_get_access_token()
            ...     # Use token for API call
            ...     headers = {"Authorization": f"Bearer {access_token}"}
            ... except AlexaTokenExpiredError:
            ...     # Trigger reauth flow
            ...     await trigger_reauth()
        """
        # Check if token is valid (not expired)
        if not await self.is_token_valid():
            _LOGGER.info("Token expired or near expiry, refreshing...")
            try:
                await self.async_refresh_token()
            except (AlexaRefreshFailedError, AlexaInvalidGrantError) as err:
                _LOGGER.error("Token refresh failed: %s", err)
                raise AlexaTokenExpiredError("Token expired and refresh failed") from err

        # Load token data from storage if not cached
        if self._token_data is None:
            self._token_data = await self._store.async_load()

        # Validate token data exists
        if not self._token_data or "access_token" not in self._token_data:
            _LOGGER.error("No access token in storage")
            raise AlexaTokenExpiredError("No access token available")

        access_token = self._token_data["access_token"]
        _LOGGER.debug("Retrieved access token from storage (token=%s...)", access_token[:8])

        return access_token

    # =========================================================================
    # Token Validation
    # =========================================================================

    async def is_token_valid(self) -> bool:
        """Check if current token is valid (not expired).

        A token is considered invalid if:
        - No token data in storage
        - Token expires within TOKEN_REFRESH_BUFFER_SECONDS

        Returns:
            True if token valid, False if expired or near expiry

        Buffer Strategy:
            Token is considered invalid if it expires within 5 minutes.
            This prevents tokens from expiring during API calls.

        Example:
            >>> if await manager.is_token_valid():
            ...     # Use existing token
            ...     token = await manager.async_get_access_token()
            ... else:
            ...     # Need to refresh
            ...     await manager.async_refresh_token()
        """
        # Load token data from storage if not cached
        if self._token_data is None:
            self._token_data = await self._store.async_load()

        # Check if token data exists
        if not self._token_data:
            _LOGGER.debug("No token data in storage")
            return False

        # Extract expires_at timestamp
        expires_at = self._token_data.get("expires_at", 0)
        if not expires_at:
            _LOGGER.debug("No expiry timestamp in token data")
            return False

        # Calculate time until expiry
        time_until_expiry = expires_at - time.time()
        is_valid = time_until_expiry > TOKEN_REFRESH_BUFFER_SECONDS

        _LOGGER.debug(
            "Token validation: %s (expires in %d seconds)",
            "valid" if is_valid else "expired/near expiry",
            int(time_until_expiry),
        )

        return is_valid

    # =========================================================================
    # Token Storage
    # =========================================================================

    async def async_save_token(self, token_response: TokenResponse) -> None:
        """Save tokens to encrypted storage.

        Stores tokens with calculated expiry timestamp. Storage is encrypted
        automatically by Home Assistant's Store class.

        Args:
            token_response: Token response from OAuth or refresh

        Storage Format:
            {
                "access_token": "Atza|...",
                "refresh_token": "Atzr|...",
                "token_type": "Bearer",
                "expires_at": 1234567890.0,  # Unix timestamp
                "scope": "alexa::skills:account_linking"
            }

        Example:
            >>> token_response = await oauth.exchange_code(...)
            >>> await manager.async_save_token(token_response)
            >>> # Tokens encrypted and saved to .storage/
        """
        # Calculate expires_at timestamp (now + expires_in)
        expires_at = time.time() + token_response.expires_in

        # Build token data dict
        self._token_data = {
            "access_token": token_response.access_token,
            "refresh_token": token_response.refresh_token,
            "token_type": token_response.token_type,
            "expires_at": expires_at,
            "scope": token_response.scope,
        }

        # Save to encrypted storage
        await self._store.async_save(self._token_data)

        _LOGGER.info(
            "Saved tokens to storage (expires in %d seconds)",
            token_response.expires_in,
        )

    # =========================================================================
    # Token Refresh
    # =========================================================================

    async def async_refresh_token(self) -> TokenResponse:
        """Refresh access token using refresh token.

        Uses the refresh token to obtain a new access token from Amazon.
        Amazon may also issue a new refresh token, which should replace
        the old one.

        Returns:
            TokenResponse with new tokens

        Raises:
            AlexaRefreshFailedError: Refresh request failed
            AlexaInvalidGrantError: Refresh token invalid/expired

        Flow:
            1. Load refresh token from storage
            2. Make refresh request to Amazon
            3. Save new tokens to storage
            4. Return token response

        Amazon Behavior:
            - Always returns new access_token
            - May return new refresh_token (update storage)
            - May return same refresh_token (still update storage)

        Example:
            >>> try:
            ...     tokens = await manager.async_refresh_token()
            ...     # New tokens automatically saved
            ... except AlexaInvalidGrantError:
            ...     # Refresh token expired, need full reauth
            ...     await trigger_reauth()
        """
        # Use lock to prevent concurrent refresh operations
        async with self._refresh_lock:
            # Load token data from storage if not cached
            if self._token_data is None:
                self._token_data = await self._store.async_load()

            # Validate token data exists
            if not self._token_data:
                _LOGGER.error("No token data available for refresh")
                raise AlexaRefreshFailedError("No token data available")

            # Extract refresh token
            refresh_token = self._token_data.get("refresh_token")
            if not refresh_token:
                _LOGGER.error("No refresh token available")
                raise AlexaRefreshFailedError("No refresh token available")

            _LOGGER.info("Refreshing access token (refresh_token=%s...)", refresh_token[:8])

            try:
                # Use OAuth manager to refresh token
                oauth_manager = self._get_oauth_manager()
                token_response = await oauth_manager.refresh_access_token(refresh_token)

                # Save new tokens to storage
                await self.async_save_token(token_response)

                _LOGGER.info("Successfully refreshed access token")

                return token_response

            except AlexaInvalidGrantError:
                _LOGGER.error("Refresh token invalid or expired")
                raise

            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.error("Network error refreshing token: %s", err)
                raise AlexaRefreshFailedError(f"Network error during token refresh: {err}") from err

            except Exception as err:
                _LOGGER.error("Unexpected error refreshing token: %s", err)
                raise AlexaRefreshFailedError(f"Unexpected error: {err}") from err

    # =========================================================================
    # Token Revocation
    # =========================================================================

    async def async_revoke_token(self) -> None:
        """Revoke refresh token with Amazon.

        Called when integration is removed to clean up tokens on Amazon's side.
        Revoking a refresh token also revokes all associated access tokens.

        Best Practice:
            Always revoke tokens when integration is removed or user disconnects.

        Example:
            >>> # User removes Alexa integration
            >>> await manager.async_revoke_token()
            >>> # Token invalid on Amazon, integration can be removed
        """
        # Load token data from storage if not cached
        if self._token_data is None:
            self._token_data = await self._store.async_load()

        # Check if token data exists
        if not self._token_data:
            _LOGGER.debug("No tokens to revoke")
            return

        # Extract refresh token
        refresh_token = self._token_data.get("refresh_token")
        if not refresh_token:
            _LOGGER.debug("No refresh token to revoke")
            return

        _LOGGER.info("Revoking refresh token (token=%s...)", refresh_token[:8])

        # Build revocation request
        session = async_get_clientsession(self.hass)
        data = {
            "token": refresh_token,
            "client_id": self.entry.data[CONF_CLIENT_ID],
            "client_secret": self.entry.data[CONF_CLIENT_SECRET],
        }

        # POST to revocation endpoint (best-effort, don't raise on failure)
        try:
            async with asyncio.timeout(10):
                async with session.post(
                    AMAZON_REVOKE_URL,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Successfully revoked token")
                    else:
                        _LOGGER.warning(
                            "Token revocation failed (status=%d)", response.status
                        )
        except Exception as err:
            _LOGGER.warning("Token revocation error: %s", err)

        # Clear token data from storage (even if revocation failed)
        self._token_data = None
        await self._store.async_remove()
        _LOGGER.info("Cleared token storage")
