"""Config flow for Alexa integration.

This module implements the Home Assistant config flow for setting up the
Alexa integration via OAuth2 with PKCE.

Flow Types:
    - User flow: Initial setup with OAuth
    - Reauth flow: Re-authenticate when tokens expire
    - Options flow: Modify integration settings
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_REDIRECT_URI,
    DOMAIN,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_INVALID_CODE,
    ERROR_INVALID_STATE,
    ERROR_UNKNOWN,
)
from .exceptions import (
    AlexaInvalidCodeError,
    AlexaInvalidGrantError,
    AlexaInvalidStateError,
    AlexaNetworkError,
    AlexaOAuthError,
)
from .oauth_manager import OAuthManager
from .token_manager import TokenManager

_LOGGER = logging.getLogger(__name__)

# Home Assistant OAuth redirect URI (standard for all integrations)
HA_OAUTH_REDIRECT_URI = "https://my.home-assistant.io/redirect/oauth"


class AlexaFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Alexa config flow with OAuth2 + PKCE.

    This flow handler implements:
    - User-initiated setup with Amazon credentials
    - OAuth2 authorization with PKCE
    - State parameter validation for CSRF protection
    - Reauth flow for expired credentials
    - Duplicate entry detection

    Flow Steps:
        1. user: Collect client_id and client_secret
        2. oauth: Handle OAuth callback
        3. reauth: Re-authenticate existing entry
        4. reauth_confirm: Confirm reauth completion

    Example:
        User initiates setup → async_step_user()
        → Redirect to Amazon OAuth → async_step_oauth()
        → Create ConfigEntry

    Security:
        - State parameter validates OAuth callback
        - PKCE prevents authorization code interception
        - Client secret encrypted in ConfigEntry
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow.

        Instance Variables:
            _oauth_manager: OAuthManager instance for OAuth operations
            _pkce_verifier: PKCE code verifier (stored during OAuth flow)
            _oauth_state: State parameter for CSRF protection
            _reauth_entry: ConfigEntry being re-authenticated (reauth flow only)
            _pending_tokens: Token response from OAuth (saved after callback)
        """
        super().__init__()
        self._oauth_manager: OAuthManager | None = None
        self._pkce_verifier: str | None = None
        self._oauth_state: str | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None
        self._pending_tokens: Any = None

    # =========================================================================
    # User Flow (Initial Setup)
    # =========================================================================

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user-initiated configuration.

        This step collects Amazon Developer credentials (client_id and
        client_secret) from the user and initiates the OAuth flow.

        Args:
            user_input: Form data from user, or None to show form

        Returns:
            FlowResult with one of:
                - Form to collect credentials (user_input is None)
                - External step to redirect to Amazon OAuth (credentials valid)
                - Error form if validation fails

        Flow:
            1. Show form to collect client_id and client_secret
            2. User submits form
            3. Initialize OAuth flow with credentials
            4. Generate PKCE verifier and challenge
            5. Generate state parameter
            6. Store OAuth state in hass.data for callback
            7. Redirect user to Amazon authorization URL

        Form Schema:
            - client_id: Amazon client ID (required)
            - client_secret: Amazon client secret (required)

        Error Codes:
            - invalid_auth: Invalid credentials format
            - cannot_connect: Network error
            - unknown: Unexpected error

        Example:
            >>> # User visits Settings → Integrations → Add Integration → Alexa
            >>> # Flow shows form with client_id and client_secret fields
            >>> # User enters credentials and clicks Submit
            >>> # Flow redirects to Amazon OAuth URL
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            # Extract credentials
            client_id = user_input[CONF_CLIENT_ID]
            client_secret = user_input[CONF_CLIENT_SECRET]

            # Validate credential format
            if not _validate_client_credentials(client_id, client_secret):
                errors["base"] = ERROR_INVALID_AUTH
            else:
                # Set unique_id to prevent duplicate entries
                await self.async_set_unique_id(client_id)
                self._abort_if_unique_id_configured()

                try:
                    # Create OAuth manager
                    self._oauth_manager = OAuthManager(
                        self.hass,
                        client_id,
                        client_secret,
                    )

                    # Generate authorization URL with PKCE
                    auth_url, code_verifier, state = await self._oauth_manager.get_authorization_url(
                        self.flow_id,
                        HA_OAUTH_REDIRECT_URI,
                    )

                    # Store OAuth state for callback validation
                    self._pkce_verifier = code_verifier
                    self._oauth_state = state

                    # Store state in hass.data for callback retrieval
                    self.hass.data.setdefault(DOMAIN, {})
                    self.hass.data[DOMAIN][self.flow_id] = {
                        "state": state,
                        "verifier": code_verifier,
                        "oauth_manager": self._oauth_manager,
                        "flow_id": self.flow_id,
                    }

                    _LOGGER.info(
                        "Initiating OAuth flow (flow_id=%s, client_id=%s...)",
                        self.flow_id,
                        client_id[:8],
                    )

                    # Redirect user to Amazon OAuth
                    return self.async_external_step(step_id="oauth", url=auth_url)

                except AlexaOAuthError as err:
                    _LOGGER.error("OAuth initialization error: %s", err)
                    errors["base"] = ERROR_CANNOT_CONNECT
                except Exception as err:
                    _LOGGER.exception("Unexpected error in user step: %s", err)
                    errors["base"] = ERROR_UNKNOWN

        # Show form to collect credentials
        data_schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): str,
                vol.Required(CONF_CLIENT_SECRET): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "docs_url": "https://developer.amazon.com/docs/login-with-amazon/",
            },
        )

    # =========================================================================
    # OAuth Callback Flow
    # =========================================================================

    async def async_step_oauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle OAuth callback from Amazon.

        After user authorizes on Amazon, they are redirected back to HA with
        an authorization code and state parameter. This step validates the
        callback and exchanges the code for tokens.

        Args:
            user_input: Callback data from Amazon redirect
                - code: Authorization code
                - state: State parameter (must match stored state)
                - error: Error code (if authorization failed)

        Returns:
            FlowResult with one of:
                - Created entry (success)
                - Abort (invalid state, OAuth error)
                - Form with error (invalid code, network error)

        Flow:
            1. Retrieve stored OAuth state from hass.data
            2. Validate state parameter matches
            3. Extract authorization code
            4. Exchange code for tokens using PKCE verifier
            5. Save tokens via TokenManager
            6. Create ConfigEntry
            7. Clean up OAuth state from hass.data

        Callback Parameters:
            Success: ?code=ANaRx...&state=xyz123...
            Error: ?error=access_denied&error_description=...&state=xyz123...

        Error Codes:
            - invalid_state: State parameter mismatch (CSRF attack?)
            - invalid_code: Authorization code invalid or expired
            - oauth_error: OAuth authorization failed
            - cannot_connect: Network error during token exchange

        Example:
            >>> # Amazon redirects to:
            >>> # https://my.home-assistant.io/redirect/oauth?
            >>> #   code=ANaRxDaHBpGQlt&state=xyzABC123
            >>> # Flow validates state, exchanges code for tokens
            >>> # Creates ConfigEntry with tokens
        """
        # Check for OAuth errors in callback
        if user_input and "error" in user_input:
            error = user_input.get("error")
            error_description = user_input.get("error_description", "No description")
            _LOGGER.error(
                "OAuth authorization failed (error=%s, description=%s)",
                error,
                error_description,
            )
            return self.async_abort(reason="oauth_error")

        # Retrieve stored OAuth state from hass.data
        oauth_data = self.hass.data.get(DOMAIN, {}).get(self.flow_id)
        if not oauth_data:
            _LOGGER.error("No OAuth state found for flow %s", self.flow_id)
            return self.async_abort(reason=ERROR_INVALID_STATE)

        # Extract stored state and verifier
        stored_state = oauth_data.get("state")
        code_verifier = oauth_data.get("verifier")
        oauth_manager = oauth_data.get("oauth_manager")

        # Validate state parameter
        callback_state = user_input.get("state") if user_input else None
        if not callback_state or not oauth_manager.validate_state(callback_state, stored_state):
            _LOGGER.error("OAuth state mismatch (CSRF protection triggered)")
            # Clean up OAuth state
            self.hass.data[DOMAIN].pop(self.flow_id, None)
            return self.async_abort(reason=ERROR_INVALID_STATE)

        # Extract authorization code
        code = user_input.get("code") if user_input else None
        if not code:
            _LOGGER.error("No authorization code in OAuth callback")
            self.hass.data[DOMAIN].pop(self.flow_id, None)
            return self.async_abort(reason="oauth_error")

        # Exchange code for tokens
        try:
            _LOGGER.info("Exchanging authorization code for tokens (code=%s...)", code[:8])

            token_response = await oauth_manager.exchange_code(
                code,
                code_verifier,
                HA_OAUTH_REDIRECT_URI,
            )

            # Store tokens temporarily for entry creation
            self._pending_tokens = token_response

            # Extract client credentials from OAuth manager
            client_id = oauth_manager.client_id
            client_secret = oauth_manager.client_secret

            # Check if this is a reauth flow
            reauth_entry_id = oauth_data.get("reauth_entry_id")
            if reauth_entry_id:
                # Update existing entry with new tokens
                entry = self.hass.config_entries.async_get_entry(reauth_entry_id)
                if entry:
                    # Save new tokens via TokenManager
                    token_manager = TokenManager(self.hass, entry)
                    await token_manager.async_save_token(token_response)

                    _LOGGER.info("Successfully updated tokens for entry %s", entry.entry_id)

                    # Clean up OAuth state
                    self.hass.data[DOMAIN].pop(self.flow_id, None)

                    return self.async_abort(reason="reauth_successful")

            # Create new ConfigEntry (initial setup)
            _LOGGER.info("Creating new Alexa integration entry")

            # Clean up OAuth state before creating entry
            self.hass.data[DOMAIN].pop(self.flow_id, None)

            # Create entry with client credentials only (not tokens)
            # Tokens will be saved by __init__.py after entry creation
            entry_data = {
                CONF_CLIENT_ID: client_id,
                CONF_CLIENT_SECRET: client_secret,
                CONF_REDIRECT_URI: HA_OAUTH_REDIRECT_URI,
            }

            # Create entry and store pending tokens in context for __init__.py
            entry = self.async_create_entry(
                title="Amazon Alexa",
                data=entry_data,
            )

            # Store pending tokens in hass.data for __init__.py to save
            self.hass.data.setdefault(DOMAIN, {})
            self.hass.data[DOMAIN][f"pending_tokens_{client_id}"] = token_response

            return entry

        except AlexaInvalidCodeError as err:
            _LOGGER.error("Invalid authorization code: %s", err)
            self.hass.data[DOMAIN].pop(self.flow_id, None)
            return self.async_abort(reason=ERROR_INVALID_CODE)

        except (AlexaNetworkError, AlexaOAuthError) as err:
            _LOGGER.error("OAuth token exchange failed: %s", err)
            self.hass.data[DOMAIN].pop(self.flow_id, None)
            return self.async_abort(reason=ERROR_CANNOT_CONNECT)

        except Exception as err:
            _LOGGER.exception("Unexpected error during OAuth callback: %s", err)
            self.hass.data[DOMAIN].pop(self.flow_id, None)
            return self.async_abort(reason=ERROR_UNKNOWN)

    # =========================================================================
    # Reauth Flow
    # =========================================================================

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauthorization request.

        Triggered when stored tokens expire or become invalid. Allows user
        to re-authenticate without removing and re-adding the integration.

        Args:
            user_input: Entry data from ConfigEntry being re-authenticated

        Returns:
            FlowResult directing to reauth_confirm step

        Flow:
            1. Store ConfigEntry being re-authenticated
            2. Show reauth_confirm step to user

        Example:
            >>> # Token refresh fails → trigger reauth
            >>> # HA shows notification: "Alexa needs reauthorization"
            >>> # User clicks "Reconfigure" → async_step_reauth()
            >>> # Flow shows confirmation → async_step_reauth_confirm()
        """
        # Get the ConfigEntry being re-authenticated
        entry_id = self.context.get("entry_id")
        if not entry_id:
            _LOGGER.error("No entry_id in reauth context")
            return self.async_abort(reason="reauth_failed")

        entry = self.hass.config_entries.async_get_entry(entry_id)
        if not entry:
            _LOGGER.error("ConfigEntry not found for reauth (entry_id=%s)", entry_id)
            return self.async_abort(reason="reauth_failed")

        # Store entry for use in reauth_confirm
        self._reauth_entry = entry

        _LOGGER.info("Initiating reauth flow for entry %s", entry_id)

        # Show reauth confirmation form
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauth confirmation.

        Shows confirmation dialog and initiates OAuth flow with existing
        credentials to obtain new tokens.

        Args:
            user_input: Form submission (or None to show form)

        Returns:
            FlowResult with one of:
                - Form to confirm reauth (user_input is None)
                - External step to redirect to Amazon OAuth (user confirmed)

        Flow:
            1. Show confirmation form
            2. User confirms → initiate OAuth with existing credentials
            3. Redirect to Amazon OAuth
            4. OAuth callback updates existing ConfigEntry

        Example:
            >>> # User sees: "Alexa authorization expired. Reauthorize?"
            >>> # User clicks "Reauthorize" → OAuth flow starts
            >>> # OAuth callback updates entry instead of creating new one
        """
        if not self._reauth_entry:
            _LOGGER.error("No reauth entry stored")
            return self.async_abort(reason="reauth_failed")

        if user_input is None:
            # Show confirmation form
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders={
                    "account": self._reauth_entry.data.get(CONF_CLIENT_ID, "Unknown"),
                },
            )

        # User confirmed reauth, initiate OAuth flow
        try:
            # Extract credentials from existing entry
            client_id = self._reauth_entry.data[CONF_CLIENT_ID]
            client_secret = self._reauth_entry.data[CONF_CLIENT_SECRET]

            # Create OAuth manager with existing credentials
            self._oauth_manager = OAuthManager(
                self.hass,
                client_id,
                client_secret,
            )

            # Generate authorization URL with PKCE
            auth_url, code_verifier, state = await self._oauth_manager.get_authorization_url(
                self.flow_id,
                HA_OAUTH_REDIRECT_URI,
            )

            # Store OAuth state for callback validation
            self._pkce_verifier = code_verifier
            self._oauth_state = state

            # Store state in hass.data for callback retrieval
            # Include reauth_entry_id to mark this as reauth flow
            self.hass.data.setdefault(DOMAIN, {})
            self.hass.data[DOMAIN][self.flow_id] = {
                "state": state,
                "verifier": code_verifier,
                "oauth_manager": self._oauth_manager,
                "flow_id": self.flow_id,
                "reauth_entry_id": self._reauth_entry.entry_id,
            }

            _LOGGER.info(
                "Initiating reauth OAuth flow (entry_id=%s)",
                self._reauth_entry.entry_id,
            )

            # Redirect user to Amazon OAuth
            return self.async_external_step(step_id="oauth", url=auth_url)

        except Exception as err:
            _LOGGER.exception("Error initiating reauth OAuth: %s", err)
            return self.async_abort(reason="reauth_failed")

    # =========================================================================
    # Static Methods
    # =========================================================================

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler.

        Args:
            config_entry: ConfigEntry instance

        Returns:
            OptionsFlow handler instance
        """
        return AlexaOptionsFlowHandler(config_entry)


class AlexaOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Alexa options flow.

    Options flow allows users to modify integration settings after initial
    setup without removing and re-adding the integration.

    Currently, Alexa integration has minimal options (Phase 1).
    This can be extended in later phases for settings like:
    - Entity filtering
    - State synchronization preferences
    - Notification settings

    Example:
        >>> # User clicks "Configure" on Alexa integration
        >>> # Options flow shows settings form
        >>> # User modifies settings and saves
        >>> # ConfigEntry.options updated
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow.

        Args:
            config_entry: ConfigEntry being configured
        """
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options.

        Args:
            user_input: Form data from user, or None to show form

        Returns:
            FlowResult with one of:
                - Form to modify options (user_input is None)
                - Entry updated (user submitted form)

        Example:
            >>> # Phase 1: Minimal options (future expansion)
            >>> # Phase 2+: Add entity filtering, sync preferences
        """
        if user_input is not None:
            # Save options and update entry
            return self.async_create_entry(title="", data=user_input)

        # Show options form (empty for Phase 1)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({}),
        )


def _validate_client_credentials(client_id: str, client_secret: str) -> bool:
    """Validate Amazon client credentials format.

    Args:
        client_id: Amazon client ID
        client_secret: Amazon client secret

    Returns:
        True if format valid, False otherwise

    Notes:
        - client_id should start with "amzn1.application-oa2-client."
        - client_secret should be 32+ characters
        - Does NOT validate credentials with Amazon (done in OAuth)
    """
    # Validate client_id format
    if not client_id.startswith("amzn1.application-oa2-client."):
        _LOGGER.warning("Invalid client_id format (missing prefix)")
        return False

    if len(client_id) < 50:
        _LOGGER.warning("Invalid client_id format (too short)")
        return False

    # Validate client_secret length
    if len(client_secret) < 32:
        _LOGGER.warning("Invalid client_secret format (too short)")
        return False

    return True
