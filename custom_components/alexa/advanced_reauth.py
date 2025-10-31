"""Advanced reauth handling for Alexa integration.

This module handles advanced reauthorization scenarios beyond simple token
refresh. It provides:

- Expired refresh token detection and handling
- App revocation detection (user revoked on Amazon)
- Client secret rotation handling
- Regional endpoint change detection
- Scope change detection
- Proactive reauth need detection
- Automatic retry with exponential backoff

Reauth Scenarios:

1. Refresh Token Expiry:
   - Refresh token has expired (>= 60 days typically)
   - Cannot renew automatically
   - Requires full OAuth flow

2. App Revocation:
   - User revoked app authorization on Amazon
   - Token refresh returns "invalid_grant"
   - Requires full reauthorization

3. Client Secret Rotation:
   - Developer rotated client secret
   - Existing tokens still work initially
   - New tokens use new secret

4. Regional Endpoint Change:
   - User changed Amazon account region
   - Endpoint mismatch detected
   - Automatic endpoint update

5. Scope Change:
   - Required OAuth scopes changed
   - Need to request new permissions
   - Requires reauthorization

Example:
    >>> handler = AdvancedReauthHandler(hass, entry)
    >>> # Proactive detection
    >>> needs_reauth = await handler.async_detect_reauth_needed()
    >>> if needs_reauth:
    ...     reason = await handler.async_detect_reauth_reason()
    ...     await handler.async_handle_reauth(reason)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    AMAZON_TOKEN_URL,
    REAUTH_BACKOFF_MULTIPLIER,
    REAUTH_MAX_RETRY_ATTEMPTS,
    REAUTH_REASON_APP_REVOKED,
    REAUTH_REASON_CLIENT_SECRET_ROTATED,
    REAUTH_REASON_REFRESH_TOKEN_EXPIRED,
    REAUTH_REASON_REGIONAL_CHANGE,
    REAUTH_REASON_SCOPE_CHANGED,
    REAUTH_RETRY_DELAY_SECONDS,
    REGIONAL_ENDPOINTS,
    REQUIRED_SCOPES,
)
from .exceptions import (
    AlexaAppRevokedError,
    AlexaClientSecretRotatedError,
    AlexaReauthError,
    AlexaReauthMaxRetriesError,
    AlexaRefreshTokenExpiredError,
    AlexaRegionalEndpointError,
    AlexaScopeChangedError,
)
from .token_manager import TokenManager

_LOGGER = logging.getLogger(__name__)


class ReauthReason(Enum):
    """Reauth reason enumeration.

    Defines all possible reasons for requiring reauthorization.
    """

    REFRESH_TOKEN_EXPIRED = REAUTH_REASON_REFRESH_TOKEN_EXPIRED
    APP_REVOKED = REAUTH_REASON_APP_REVOKED
    CLIENT_SECRET_ROTATED = REAUTH_REASON_CLIENT_SECRET_ROTATED
    REGIONAL_CHANGE = REAUTH_REASON_REGIONAL_CHANGE
    SCOPE_CHANGED = REAUTH_REASON_SCOPE_CHANGED


@dataclass
class ReauthResult:
    """Reauth operation result.

    Attributes:
        success: Whether reauth succeeded
        reason: Reason for reauth
        error: Error message if failed
        retry_count: Number of retry attempts made
        new_region: New region if regional change detected
    """

    success: bool
    reason: ReauthReason | None = None
    error: str | None = None
    retry_count: int = 0
    new_region: str | None = None


class AdvancedReauthHandler:
    """Handles advanced reauthorization scenarios.

    This class provides comprehensive reauth handling beyond simple token
    refresh. It detects various failure scenarios and initiates appropriate
    recovery actions.

    Features:
        - Proactive reauth need detection
        - Scenario-specific error handling
        - Automatic retry with exponential backoff
        - Regional endpoint auto-correction
        - Scope change detection
        - App revocation detection

    Thread Safety:
        - Uses asyncio locks to prevent concurrent reauth
        - Safe for multiple simultaneous calls

    Example:
        >>> handler = AdvancedReauthHandler(hass, entry)
        >>> # Detect if reauth needed
        >>> if await handler.async_detect_reauth_needed():
        ...     reason = await handler.async_detect_reauth_reason()
        ...     result = await handler.async_handle_reauth(reason)
        ...     if result.success:
        ...         print("Reauth successful")
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize advanced reauth handler.

        Args:
            hass: Home Assistant instance
            entry: ConfigEntry for this integration instance
        """
        self.hass = hass
        self.entry = entry
        self._token_manager = TokenManager(hass, entry)
        self._reauth_lock = asyncio.Lock()

    async def async_detect_reauth_needed(self) -> bool:
        """Proactively detect if reauth is needed.

        Checks multiple indicators:
            - Refresh token timestamp vs expiry
            - Token validation response codes
            - Scope mismatches
            - Regional endpoint mismatches

        Returns:
            True if reauth needed, False otherwise

        Example:
            >>> if await handler.async_detect_reauth_needed():
            ...     print("Reauth required")
        """
        try:
            # Check 1: Token expiry
            token_data = await self._token_manager._store.async_load()
            if not token_data:
                _LOGGER.debug("No token data found, reauth needed")
                return True

            # Check 2: Refresh token age (typically expires after 60 days)
            if "refresh_token_timestamp" in token_data:
                refresh_age = time.time() - token_data["refresh_token_timestamp"]
                # Amazon refresh tokens typically expire after 60 days
                if refresh_age > (60 * 24 * 60 * 60):
                    _LOGGER.warning(
                        "Refresh token older than 60 days (%d days), may be expired",
                        refresh_age / (24 * 60 * 60),
                    )
                    return True

            # Check 3: Try to validate token
            try:
                access_token = await self._token_manager.async_get_access_token()
                if not access_token:
                    _LOGGER.debug("No valid access token, reauth needed")
                    return True
            except Exception as err:
                _LOGGER.debug("Token validation failed: %s, reauth needed", err)
                return True

            # Check 4: Scope validation
            current_scope = token_data.get("scope", "")
            if REQUIRED_SCOPES not in current_scope:
                _LOGGER.warning(
                    "Required scope '%s' not in current scope '%s', reauth needed",
                    REQUIRED_SCOPES,
                    current_scope,
                )
                return True

            _LOGGER.debug("Reauth not needed, tokens valid")
            return False

        except Exception as err:
            _LOGGER.error("Error detecting reauth need: %s", err)
            return True  # Conservative: assume reauth needed on error

    async def async_detect_reauth_reason(self) -> ReauthReason:
        """Detect specific reason for reauth.

        Analyzes error conditions to determine root cause:
            - API response codes
            - Token age
            - Scope mismatches
            - Endpoint errors

        Returns:
            ReauthReason enum value

        Example:
            >>> reason = await handler.async_detect_reauth_reason()
            >>> if reason == ReauthReason.APP_REVOKED:
            ...     print("User revoked app on Amazon")
        """
        try:
            token_data = await self._token_manager._store.async_load()

            # Check 1: Refresh token age
            if token_data and "refresh_token_timestamp" in token_data:
                refresh_age = time.time() - token_data["refresh_token_timestamp"]
                if refresh_age > (60 * 24 * 60 * 60):
                    _LOGGER.info("Detected reason: Refresh token expired")
                    return ReauthReason.REFRESH_TOKEN_EXPIRED

            # Check 2: Try token refresh and analyze error
            try:
                await self._token_manager.async_refresh_token()
            except Exception as err:
                error_str = str(err).lower()

                if "invalid_grant" in error_str:
                    _LOGGER.info("Detected reason: App revoked (invalid_grant)")
                    return ReauthReason.APP_REVOKED

                if "invalid_client" in error_str:
                    _LOGGER.info("Detected reason: Client secret rotated")
                    return ReauthReason.CLIENT_SECRET_ROTATED

                if "region" in error_str or "endpoint" in error_str:
                    _LOGGER.info("Detected reason: Regional endpoint change")
                    return ReauthReason.REGIONAL_CHANGE

            # Check 3: Scope validation
            if token_data:
                current_scope = token_data.get("scope", "")
                if REQUIRED_SCOPES not in current_scope:
                    _LOGGER.info("Detected reason: Scope changed")
                    return ReauthReason.SCOPE_CHANGED

            # Default: assume refresh token expired
            _LOGGER.info("Detected reason: Refresh token expired (default)")
            return ReauthReason.REFRESH_TOKEN_EXPIRED

        except Exception as err:
            _LOGGER.error("Error detecting reauth reason: %s", err)
            return ReauthReason.REFRESH_TOKEN_EXPIRED

    async def async_handle_expired_refresh_token(self) -> ReauthResult:
        """Handle expired refresh token scenario.

        Refresh tokens typically expire after 60 days of inactivity.
        This cannot be renewed automatically - requires full OAuth flow.

        Returns:
            ReauthResult with operation status

        Raises:
            AlexaRefreshTokenExpiredError: If handling fails

        Example:
            >>> result = await handler.async_handle_expired_refresh_token()
            >>> if result.success:
            ...     print("Reauth flow initiated")
        """
        try:
            _LOGGER.warning(
                "Refresh token expired, initiating full reauth flow for entry %s",
                self.entry.entry_id,
            )

            # Trigger reauth flow
            await self.hass.config_entries.flow.async_init(
                self.entry.domain,
                context={
                    "source": "reauth",
                    "entry_id": self.entry.entry_id,
                    "reason": REAUTH_REASON_REFRESH_TOKEN_EXPIRED,
                },
                data=self.entry.data,
            )

            return ReauthResult(
                success=True,
                reason=ReauthReason.REFRESH_TOKEN_EXPIRED,
            )

        except Exception as err:
            _LOGGER.error("Failed to handle expired refresh token: %s", err)
            raise AlexaRefreshTokenExpiredError(
                f"Refresh token expired and reauth failed: {err}"
            ) from err

    async def async_handle_revoked_app(self) -> ReauthResult:
        """Handle app revocation scenario.

        User revoked app authorization on Amazon website. All tokens are
        invalid and cannot be refreshed. Requires full reauthorization.

        Returns:
            ReauthResult with operation status

        Raises:
            AlexaAppRevokedError: If handling fails

        Example:
            >>> result = await handler.async_handle_revoked_app()
            >>> if result.success:
            ...     print("Reauth flow initiated for revoked app")
        """
        try:
            _LOGGER.warning(
                "App authorization revoked by user, initiating reauth for entry %s",
                self.entry.entry_id,
            )

            # Clear invalid tokens
            await self._token_manager._store.async_remove()

            # Trigger reauth flow
            await self.hass.config_entries.flow.async_init(
                self.entry.domain,
                context={
                    "source": "reauth",
                    "entry_id": self.entry.entry_id,
                    "reason": REAUTH_REASON_APP_REVOKED,
                },
                data=self.entry.data,
            )

            return ReauthResult(
                success=True,
                reason=ReauthReason.APP_REVOKED,
            )

        except Exception as err:
            _LOGGER.error("Failed to handle revoked app: %s", err)
            raise AlexaAppRevokedError(
                f"App revoked and reauth failed: {err}"
            ) from err

    async def async_handle_client_secret_rotation(self) -> ReauthResult:
        """Handle client secret rotation scenario.

        Developer rotated the client secret in Amazon developer console.
        Existing tokens still work but new tokens require new secret.

        This scenario is transparent to users - we detect the new secret
        from config and use it for future token operations.

        Returns:
            ReauthResult with operation status

        Raises:
            AlexaClientSecretRotatedError: If handling fails

        Example:
            >>> result = await handler.async_handle_client_secret_rotation()
            >>> if result.success:
            ...     print("Using new client secret")
        """
        try:
            _LOGGER.info(
                "Client secret rotated, updating config for entry %s",
                self.entry.entry_id,
            )

            # Get new client secret from config
            new_secret = self.entry.data.get(CONF_CLIENT_SECRET)

            if not new_secret:
                raise AlexaClientSecretRotatedError(
                    "New client secret not found in config"
                )

            # Verify new secret works by attempting token refresh
            try:
                await self._token_manager.async_refresh_token()
                _LOGGER.info("Client secret rotation successful")

                return ReauthResult(
                    success=True,
                    reason=ReauthReason.CLIENT_SECRET_ROTATED,
                )

            except Exception as refresh_err:
                _LOGGER.warning(
                    "Token refresh failed with new secret, initiating full reauth: %s",
                    refresh_err,
                )

                # If refresh fails, initiate full reauth
                await self.hass.config_entries.flow.async_init(
                    self.entry.domain,
                    context={
                        "source": "reauth",
                        "entry_id": self.entry.entry_id,
                        "reason": REAUTH_REASON_CLIENT_SECRET_ROTATED,
                    },
                    data=self.entry.data,
                )

                return ReauthResult(
                    success=True,
                    reason=ReauthReason.CLIENT_SECRET_ROTATED,
                )

        except Exception as err:
            _LOGGER.error("Failed to handle client secret rotation: %s", err)
            raise AlexaClientSecretRotatedError(
                f"Client secret rotation failed: {err}"
            ) from err

    async def async_handle_regional_change(self) -> ReauthResult:
        """Handle regional endpoint change scenario.

        User changed Amazon account region (e.g., NA -> EU).
        API endpoints need to be updated to match new region.

        Detects new region from error responses and updates config.

        Returns:
            ReauthResult with new region

        Raises:
            AlexaRegionalEndpointError: If handling fails

        Example:
            >>> result = await handler.async_handle_regional_change()
            >>> if result.success:
            ...     print(f"Updated to region: {result.new_region}")
        """
        try:
            _LOGGER.info(
                "Regional endpoint change detected for entry %s",
                self.entry.entry_id,
            )

            # Detect new region by trying each endpoint
            new_region = await self._detect_correct_region()

            if not new_region:
                raise AlexaRegionalEndpointError(
                    "Could not detect correct region"
                )

            _LOGGER.info("Detected new region: %s", new_region)

            # Update config entry with new region
            new_data = {**self.entry.data, "region": new_region}
            self.hass.config_entries.async_update_entry(
                self.entry,
                data=new_data,
            )

            # Retry token refresh with new endpoint
            await self._token_manager.async_refresh_token()

            return ReauthResult(
                success=True,
                reason=ReauthReason.REGIONAL_CHANGE,
                new_region=new_region,
            )

        except Exception as err:
            _LOGGER.error("Failed to handle regional change: %s", err)
            raise AlexaRegionalEndpointError(
                f"Regional endpoint change failed: {err}"
            ) from err

    async def async_handle_reauth(
        self,
        reason: ReauthReason,
        retry_count: int = 0,
    ) -> ReauthResult:
        """Handle reauth with retry logic.

        Dispatches to appropriate handler based on reason.
        Implements exponential backoff retry strategy.

        Args:
            reason: ReauthReason enum value
            retry_count: Current retry attempt (for recursion)

        Returns:
            ReauthResult with operation status

        Raises:
            AlexaReauthMaxRetriesError: If max retries exceeded
            AlexaReauthError: If reauth fails

        Example:
            >>> reason = await handler.async_detect_reauth_reason()
            >>> result = await handler.async_handle_reauth(reason)
            >>> if not result.success:
            ...     print(f"Reauth failed: {result.error}")
        """
        if retry_count >= REAUTH_MAX_RETRY_ATTEMPTS:
            raise AlexaReauthMaxRetriesError(
                f"Max reauth retries ({REAUTH_MAX_RETRY_ATTEMPTS}) exceeded"
            )

        if self._reauth_lock.locked():
            _LOGGER.debug("Reauth already in progress, waiting...")
            async with self._reauth_lock:
                pass  # Wait for current reauth to complete
            return ReauthResult(success=True, reason=reason)

        # Track if we need to retry after releasing lock
        retry_needed = False
        caught_exception = None

        async with self._reauth_lock:
            try:
                _LOGGER.info(
                    "Handling reauth: reason=%s, retry=%d",
                    reason.value,
                    retry_count,
                )

                # Dispatch to specific handler
                if reason == ReauthReason.REFRESH_TOKEN_EXPIRED:
                    return await self.async_handle_expired_refresh_token()

                elif reason == ReauthReason.APP_REVOKED:
                    return await self.async_handle_revoked_app()

                elif reason == ReauthReason.CLIENT_SECRET_ROTATED:
                    return await self.async_handle_client_secret_rotation()

                elif reason == ReauthReason.REGIONAL_CHANGE:
                    return await self.async_handle_regional_change()

                elif reason == ReauthReason.SCOPE_CHANGED:
                    # Scope change requires full reauth
                    await self.hass.config_entries.flow.async_init(
                        self.entry.domain,
                        context={
                            "source": "reauth",
                            "entry_id": self.entry.entry_id,
                            "reason": REAUTH_REASON_SCOPE_CHANGED,
                        },
                        data=self.entry.data,
                    )
                    return ReauthResult(success=True, reason=reason)

                else:
                    raise AlexaReauthError(f"Unknown reauth reason: {reason}")

            except AlexaReauthMaxRetriesError:
                raise

            except Exception as err:
                _LOGGER.error("Reauth failed (retry %d): %s", retry_count, err)
                # Mark for retry OUTSIDE the lock to prevent deadlock
                retry_needed = True
                caught_exception = err

        # Retry logic OUTSIDE the lock to prevent deadlock
        if retry_needed:
            # Exponential backoff
            delay = REAUTH_RETRY_DELAY_SECONDS * (
                REAUTH_BACKOFF_MULTIPLIER ** retry_count
            )
            _LOGGER.info("Retrying reauth in %d seconds...", delay)
            await asyncio.sleep(delay)

            # Recursive retry (lock is now released, safe to retry)
            return await self.async_handle_reauth(reason, retry_count + 1)

    async def _detect_correct_region(self) -> str | None:
        """Detect correct Amazon region by testing endpoints.

        Tries token validation against each regional endpoint to find
        the one that works.

        Returns:
            Region code (na/eu/fe) or None if not detected
        """
        session = async_get_clientsession(self.hass)
        token_data = await self._token_manager._store.async_load()

        if not token_data or "refresh_token" not in token_data:
            _LOGGER.debug("No refresh token available for region detection")
            return None

        refresh_token = token_data["refresh_token"]
        client_id = self.entry.data[CONF_CLIENT_ID]
        client_secret = self.entry.data[CONF_CLIENT_SECRET]

        # Try each region
        for region, endpoints in REGIONAL_ENDPOINTS.items():
            try:
                _LOGGER.debug("Testing region: %s", region)

                async with session.post(
                    endpoints["token_url"],
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Detected correct region: %s", region)
                        return region

            except Exception as err:
                _LOGGER.debug("Region %s failed: %s", region, err)
                continue

        _LOGGER.warning("Could not detect correct region")
        return None
