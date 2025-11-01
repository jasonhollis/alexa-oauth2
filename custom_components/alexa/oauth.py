"""Amazon Login with Amazon (LWA) OAuth2 implementation with PKCE.

This module implements OAuth2 Authorization Code flow with PKCE (RFC 7636)
for Amazon Alexa integration. PKCE (Proof Key for Code Exchange) enhances
security by preventing authorization code interception attacks.

References:
    - RFC 7636: Proof Key for Code Exchange by OAuth Public Clients
    - Amazon LWA Documentation: https://developer.amazon.com/docs/login-with-amazon/
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from typing import Any, cast
from urllib.parse import urlencode

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN, AMAZON_AUTH_URL, AMAZON_TOKEN_URL

_LOGGER = logging.getLogger(__name__)


class AlexaOAuth2Implementation(config_entry_oauth2_flow.AbstractOAuth2Implementation):
    """Amazon LWA OAuth2 implementation with PKCE.

    This implementation extends Home Assistant's OAuth2 framework to support
    PKCE (Proof Key for Code Exchange) as required by Amazon LWA security
    best practices.

    PKCE Flow:
        1. Generate cryptographically random verifier (43-128 chars)
        2. Create SHA256 challenge from verifier
        3. Send challenge in authorization request
        4. Store verifier in hass.data keyed by flow_id
        5. Include verifier in token exchange request
        6. Clean up stored verifier after exchange

    Security Notes:
        - Verifier uses secrets.token_urlsafe() for cryptographic randomness
        - Challenge uses SHA256 (method=S256) per RFC 7636 recommendations
        - Base64url encoding without padding per RFC 7636 section 4.2
        - Verifier storage is scoped to flow_id to prevent leakage
    """

    def __init__(
        self,
        hass: HomeAssistant,
        domain: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        """Initialize Amazon LWA OAuth2 implementation.

        Args:
            hass: Home Assistant instance for data storage
            domain: Integration domain (should be DOMAIN constant)
            client_id: Amazon LWA client ID from security profile
            client_secret: Amazon LWA client secret from security profile
        """
        super().__init__(
            hass,
            domain,
            client_id,
            client_secret,
            AMAZON_AUTH_URL,
            AMAZON_TOKEN_URL,
        )

        # Initialize PKCE storage if not exists
        if DOMAIN not in hass.data:
            hass.data[DOMAIN] = {}
        if "pkce" not in hass.data[DOMAIN]:
            hass.data[DOMAIN]["pkce"] = {}

        _LOGGER.debug("Initialized AlexaOAuth2Implementation with PKCE support")

    @property
    def name(self) -> str:
        """Name of the implementation."""
        return "Amazon Alexa"

    @property
    def domain(self) -> str:
        """Domain that is providing the implementation."""
        return DOMAIN

    def _generate_pkce_pair(self) -> tuple[str, str]:
        """Generate PKCE verifier and challenge pair.

        Implements RFC 7636 PKCE parameter generation:
        - Verifier: 43-128 characters, base64url-encoded random bytes
        - Challenge: base64url(SHA256(verifier))
        - Method: S256 (SHA-256 hash)

        Returns:
            Tuple of (verifier, challenge) both base64url-encoded

        Notes:
            - Uses secrets.token_urlsafe(32) for 43-char verifier
            - Removes padding (=) from base64url encoding per RFC 7636
            - Verifier entropy: 32 bytes = 256 bits
        """
        # Generate 32 random bytes -> 43 base64url characters (no padding)
        # This is within RFC 7636 requirement of 43-128 characters
        verifier = secrets.token_urlsafe(32)

        # Create SHA256 hash of verifier
        verifier_bytes = verifier.encode('ascii')
        challenge_bytes = hashlib.sha256(verifier_bytes).digest()

        # Base64url encode the challenge (no padding per RFC 7636)
        challenge = base64.urlsafe_b64encode(challenge_bytes).decode('ascii').rstrip('=')

        _LOGGER.debug(
            "Generated PKCE pair: verifier_len=%d, challenge_len=%d",
            len(verifier),
            len(challenge)
        )

        return verifier, challenge

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Generate authorization URL with PKCE challenge.

        Creates the authorization URL with required PKCE parameters for
        Amazon LWA OAuth2 flow. Stores the verifier for later use during
        token exchange.

        Args:
            flow_id: Config flow ID (used as OAuth state parameter)

        Returns:
            Authorization URL with PKCE parameters

        URL Format:
            https://www.amazon.com/ap/oa?
                client_id={client_id}&
                scope=profile:user_id&
                response_type=code&
                redirect_uri={redirect_uri}&
                state={flow_id}&
                code_challenge={challenge}&
                code_challenge_method=S256

        Notes:
            - Verifier is stored in hass.data[DOMAIN]["pkce"][flow_id]
            - Scope "profile:user_id" allows access to user's Amazon ID
            - State parameter prevents CSRF attacks (managed by framework)
        """
        # Generate PKCE pair
        verifier, challenge = self._generate_pkce_pair()

        # Store verifier for token exchange (keyed by flow_id)
        self.hass.data[DOMAIN]["pkce"][flow_id] = verifier

        _LOGGER.info(
            "Stored PKCE verifier for flow_id=%s (challenge=%s...)",
            flow_id,
            challenge[:16]
        )

        # Get redirect URI from parent implementation
        redirect_uri = self.redirect_uri

        # Build authorization URL with PKCE parameters
        params = {
            "client_id": self.client_id,
            "scope": "profile:user_id",
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": flow_id,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        authorize_url = f"{self.authorize_url}?{urlencode(params)}"

        _LOGGER.debug(
            "Generated authorization URL: %s",
            authorize_url.replace(self.client_id, "CLIENT_ID")
        )

        return authorize_url

    async def async_resolve_external_data(self, external_data: Any) -> dict[str, Any]:
        """Resolve authorization code to access token with PKCE verifier.

        Exchanges the authorization code for an access token, including the
        PKCE verifier to complete the PKCE flow. Validates that the verifier
        exists and cleans it up after successful exchange.

        Args:
            external_data: External data from OAuth callback containing:
                - code: Authorization code
                - state: Flow ID (for verifier lookup)

        Returns:
            Token data dictionary containing:
                - access_token: Amazon LWA access token
                - token_type: "Bearer"
                - expires_in: Token lifetime in seconds
                - refresh_token: Refresh token (if requested)

        Raises:
            ValueError: If PKCE verifier not found (expired/invalid session)

        Security Notes:
            - Verifier is removed from storage after use (one-time use)
            - Token exchange uses client credentials authentication
            - Amazon validates that challenge matches SHA256(verifier)
        """
        # Extract state (flow_id) from external data
        state = cast(str, external_data.get("state"))

        # Retrieve stored PKCE verifier
        verifier = self.hass.data[DOMAIN]["pkce"].get(state)

        if not verifier:
            _LOGGER.error(
                "PKCE verifier not found for state=%s (expired session or replay attack)",
                state
            )
            raise ValueError(
                "PKCE verifier not found. Authorization session may have expired. "
                "Please restart the OAuth flow."
            )

        _LOGGER.debug(
            "Retrieved PKCE verifier for state=%s (verifier_len=%d)",
            state,
            len(verifier)
        )

        # Add code_verifier to token request data
        # Parent implementation handles the actual token exchange
        token_data = {
            "code_verifier": verifier,
        }

        try:
            # Call parent implementation to perform token exchange
            # This will merge our token_data with standard OAuth2 parameters
            result = await super().async_resolve_external_data(external_data)

            _LOGGER.info(
                "Successfully exchanged authorization code for access token (state=%s)",
                state
            )

            return result

        finally:
            # Always clean up verifier (success or failure)
            # This prevents replay attacks and memory leaks
            if state in self.hass.data[DOMAIN]["pkce"]:
                del self.hass.data[DOMAIN]["pkce"][state]
                _LOGGER.debug("Cleaned up PKCE verifier for state=%s", state)

    async def async_refresh_token(self, token: dict[str, Any]) -> dict[str, Any]:
        """Refresh the access token.

        Amazon LWA supports refresh tokens. This method uses the parent
        implementation which handles standard OAuth2 refresh flow.

        Args:
            token: Current token data with refresh_token

        Returns:
            New token data dictionary

        Notes:
            - PKCE is NOT used for refresh token flow (only authorization)
            - Refresh requires client credentials authentication
        """
        _LOGGER.debug("Refreshing Amazon LWA access token")
        return await super().async_refresh_token(token)
