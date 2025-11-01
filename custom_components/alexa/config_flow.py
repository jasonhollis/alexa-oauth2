"""Config flow for Amazon Alexa integration.

This module implements the Home Assistant config flow for setting up the
Amazon Alexa integration using the built-in OAuth2 framework with our
custom PKCE implementation.

Flow Types:
    - User flow: Initial setup via OAuth2
    - Reauth flow: Re-authenticate when tokens expire

Security Features:
    - OAuth2 Authorization Code flow with PKCE (RFC 7636)
    - State parameter for CSRF protection (managed by framework)
    - Unique ID based on Amazon user_id (prevents duplicate accounts)
    - Client credentials encrypted in ConfigEntry storage
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError

from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, REQUIRED_SCOPES

_LOGGER = logging.getLogger(__name__)


class AlexaFlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Handle Amazon Alexa OAuth2 config flow.

    This flow handler uses Home Assistant's built-in OAuth2 framework with
    our custom AlexaOAuth2Implementation (defined in oauth.py) that provides
    PKCE support for Amazon Login with Amazon (LWA) security requirements.

    Flow Steps:
        1. User enters client_id and client_secret
        2. Framework redirects to Amazon OAuth with PKCE challenge
        3. User authorizes on Amazon's site
        4. Amazon redirects back with authorization code
        5. Framework exchanges code for tokens (with PKCE verifier)
        6. We fetch Amazon user profile for unique_id
        7. ConfigEntry created with tokens

    Security:
        - PKCE (Proof Key for Code Exchange) prevents authorization code interception
        - State parameter prevents CSRF attacks (framework handles this)
        - Unique ID based on Amazon user_id prevents duplicate accounts
        - Client credentials encrypted in config entry storage

    Example:
        >>> # User clicks Settings → Integrations → Add Integration → Alexa
        >>> # Flow shows form for client_id and client_secret
        >>> # User submits → redirect to Amazon OAuth
        >>> # User authorizes → redirect back to HA
        >>> # Flow creates ConfigEntry with encrypted tokens
    """

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        """Return logger for this flow."""
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Extra data to append to authorization URL.

        This provides the OAuth scope to request from Amazon.
        Scope 'profile:user_id' allows access to user's Amazon ID and profile.

        Returns:
            Dictionary with scope parameter for authorization URL

        Notes:
            - Scope must match Amazon LWA security profile configuration
            - Required scope defined in const.py: REQUIRED_SCOPES
            - Framework automatically includes this in authorization URL
        """
        return {"scope": REQUIRED_SCOPES}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initialized by the user.

        This is the entry point for the config flow. Since we're using the
        OAuth2 framework, we don't need the "already configured" check because
        unique_id is set in async_oauth_create_entry based on Amazon user_id,
        which properly prevents duplicate accounts.

        Args:
            user_input: Not used (framework handles OAuth flow)

        Returns:
            FlowResult directing to OAuth flow

        Flow:
            1. User initiates integration
            2. Framework calls async_step_user
            3. Framework redirects to OAuth (using our AlexaOAuth2Implementation)
            4. After OAuth completes, async_oauth_create_entry is called

        Notes:
            - Multiple Amazon accounts are supported (different user_ids)
            - Same account cannot be added twice (unique_id check)
            - Framework handles all OAuth mechanics (PKCE, state, redirect)
        """
        return await super().async_step_user(user_input)

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> FlowResult:
        """Create an entry for Amazon Alexa after OAuth completes.

        This is called by the framework after successful OAuth token exchange.
        We fetch the Amazon user profile to get a unique user_id for duplicate
        detection and account identification.

        Args:
            data: OAuth data from framework containing:
                - token: Access token data (access_token, refresh_token, expires_in)
                - auth_implementation: Implementation domain (DOMAIN)

        Returns:
            FlowResult with one of:
                - Created entry (success)
                - Abort (cannot connect, invalid auth, or duplicate account)

        Flow:
            1. Extract access_token from data
            2. Call Amazon profile API to get user_id
            3. Set unique_id to prevent duplicate accounts
            4. Create ConfigEntry with tokens and profile data

        Error Handling:
            - cannot_connect: Network error fetching profile
            - invalid_auth: Amazon returned error or missing user_id
            - already_configured: Same Amazon account already added (via unique_id)

        Example:
            >>> # After OAuth completes:
            >>> data = {
            ...     "token": {
            ...         "access_token": "Atza|...",
            ...         "refresh_token": "Atzr|...",
            ...         "expires_in": 3600,
            ...         "token_type": "Bearer"
            ...     },
            ...     "auth_implementation": "alexa"
            ... }
            >>> # Flow fetches profile, creates entry
        """
        session = async_get_clientsession(self.hass)
        token = data["token"]

        # Fetch Amazon user profile to get unique user_id
        headers = {
            "Authorization": f"Bearer {token['access_token']}",
        }

        try:
            async with session.get(
                "https://api.amazon.com/user/profile",
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error(
                        "Failed to fetch Amazon user profile (status=%d)", resp.status
                    )
                    return self.async_abort(reason="cannot_connect")

                profile = await resp.json()

        except ClientError as err:
            _LOGGER.error("Network error fetching Amazon user profile: %s", err)
            return self.async_abort(reason="cannot_connect")

        except Exception as err:
            _LOGGER.exception("Unexpected error fetching Amazon user profile: %s", err)
            return self.async_abort(reason="invalid_auth")

        # Extract user_id for unique identification
        user_id = profile.get("user_id")
        if not user_id:
            _LOGGER.error("Amazon profile missing user_id: %s", profile)
            return self.async_abort(reason="invalid_auth")

        # Set unique_id to prevent duplicate accounts
        # This ensures the same Amazon account cannot be added twice
        await self.async_set_unique_id(user_id)
        self._abort_if_unique_id_configured()

        _LOGGER.info(
            "Creating Alexa integration entry for user %s (user_id=%s)",
            profile.get("name", "Unknown"),
            user_id[:8],  # Log partial ID for privacy
        )

        # Create config entry with OAuth tokens and profile data
        # Framework automatically saves tokens in encrypted storage
        return self.async_create_entry(
            title=f"Amazon Alexa ({profile.get('name', 'User')})",
            data={
                "auth_implementation": DOMAIN,
                "token": token,
                "user_id": user_id,
                "name": profile.get("name"),
                "email": profile.get("email"),
            },
        )
