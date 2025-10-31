"""OAuth2 Manager for Amazon Alexa with PKCE.

This module implements OAuth2 Authorization Code flow with PKCE (Proof Key for
Code Exchange) as required by Amazon Login with Amazon (LWA).

SECURITY: This implementation follows RFC 7636 for PKCE to prevent
authorization code interception attacks.

References:
    - OAuth 2.0: https://tools.ietf.org/html/rfc6749
    - PKCE: https://tools.ietf.org/html/rfc7636
    - Amazon LWA: https://developer.amazon.com/docs/login-with-amazon/
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import aiohttp
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    AMAZON_AUTH_URL,
    AMAZON_TOKEN_URL,
    REQUIRED_SCOPES,
    TOKEN_EXCHANGE_TIMEOUT_SECONDS,
    TOKEN_REFRESH_TIMEOUT_SECONDS,
)
from .exceptions import (
    AlexaInvalidCodeError,
    AlexaInvalidGrantError,
    AlexaNetworkError,
    AlexaOAuthError,
    AlexaTimeoutError,
)

_LOGGER = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class TokenResponse:
    """OAuth token response from Amazon LWA.

    Attributes:
        access_token: OAuth access token (prefix: Atza|)
        refresh_token: Refresh token for obtaining new access tokens (prefix: Atzr|)
        token_type: Token type (always "Bearer")
        expires_in: Seconds until access token expires (typically 3600)
        scope: Granted scopes (space-separated)
    """

    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    scope: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenResponse:
        """Parse token response from Amazon JSON.

        Args:
            data: JSON response from Amazon token endpoint

        Returns:
            Parsed TokenResponse

        Raises:
            KeyError: If required fields missing
            ValueError: If data format invalid
        """
        required_fields = ["access_token", "refresh_token", "token_type", "expires_in"]
        missing_fields = [field for field in required_fields if field not in data]

        if missing_fields:
            raise ValueError(
                f"Missing required fields in token response: {missing_fields}"
            )

        # Validate token type
        if data["token_type"] != "Bearer":
            raise ValueError(
                f"Invalid token_type: {data['token_type']}, expected 'Bearer'"
            )

        # Validate expires_in is positive integer
        expires_in = data["expires_in"]
        if not isinstance(expires_in, int) or expires_in <= 0:
            raise ValueError(
                f"Invalid expires_in: {expires_in}, must be positive integer"
            )

        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            token_type=data["token_type"],
            expires_in=expires_in,
            scope=data.get("scope", ""),
        )


# =============================================================================
# OAuth Manager
# =============================================================================


class OAuthManager:
    """Manages OAuth2 with PKCE flow for Amazon Alexa.

    This class handles:
    - PKCE code verifier/challenge generation (RFC 7636)
    - State parameter generation for CSRF protection
    - Authorization URL construction
    - Authorization code exchange for tokens
    - Token refresh
    - Token validation

    Security Features:
    - PKCE prevents authorization code interception
    - State parameter prevents CSRF attacks
    - Cryptographic randomness for all secret values
    - Constant-time comparison for state validation
    - No client secrets logged or exposed

    Example:
        >>> oauth = OAuthManager(hass, client_id, client_secret)
        >>> auth_url, code_verifier = await oauth.get_authorization_url(
        ...     flow_id, redirect_uri
        ... )
        >>> # User authorizes at auth_url, callback received with code
        >>> tokens = await oauth.exchange_code(code, code_verifier, redirect_uri)
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client_id: str,
        client_secret: str,
    ) -> None:
        """Initialize OAuth manager.

        Args:
            hass: Home Assistant instance
            client_id: Amazon client ID (format: amzn1.application-oa2-client.*)
            client_secret: Amazon client secret
        """
        self.hass = hass
        self.client_id = client_id
        self.client_secret = client_secret

    # =========================================================================
    # PKCE Implementation (RFC 7636)
    # =========================================================================

    def generate_pkce_pair(self) -> tuple[str, str]:
        """Generate PKCE code_verifier and code_challenge.

        PKCE (Proof Key for Code Exchange) prevents authorization code
        interception attacks by requiring the client to prove possession of
        a secret value used in the authorization request.

        Algorithm:
            1. Generate 32-byte cryptographically random code_verifier
            2. Base64url encode verifier (without padding)
            3. SHA-256 hash the verifier
            4. Base64url encode hash as code_challenge

        Returns:
            Tuple of (code_verifier, code_challenge)
                - code_verifier: 43-character base64url string
                - code_challenge: 43-character base64url SHA-256 hash

        Security:
            - Uses secrets.token_bytes() for cryptographic randomness
            - Meets RFC 7636 requirements (43-128 character verifier)
            - Uses S256 challenge method (SHA-256)

        Example:
            >>> verifier, challenge = oauth.generate_pkce_pair()
            >>> len(verifier)  # 43 characters (32 bytes base64url)
            43
            >>> # Verify challenge is SHA-256 of verifier
            >>> import hashlib
            >>> import base64
            >>> computed = base64.urlsafe_b64encode(
            ...     hashlib.sha256(verifier.encode()).digest()
            ... ).decode().rstrip('=')
            >>> computed == challenge
            True

        References:
            - RFC 7636: https://tools.ietf.org/html/rfc7636
        """
        # Generate 32-byte random verifier (256 bits of entropy)
        verifier_bytes = secrets.token_bytes(32)
        code_verifier = (
            base64.urlsafe_b64encode(verifier_bytes).decode("utf-8").rstrip("=")
        )

        # Generate SHA-256 challenge from verifier
        challenge_bytes = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = (
            base64.urlsafe_b64encode(challenge_bytes).decode("utf-8").rstrip("=")
        )

        _LOGGER.debug(
            "Generated PKCE pair (verifier_len=%d, challenge_len=%d)",
            len(code_verifier),
            len(code_challenge),
        )

        return code_verifier, code_challenge

    def generate_state(self) -> str:
        """Generate cryptographically random state parameter.

        The state parameter prevents CSRF (Cross-Site Request Forgery) attacks
        by ensuring the OAuth callback corresponds to the original request.

        Returns:
            43-character base64url-encoded random state

        Security:
            - 32 bytes (256 bits) of cryptographic randomness
            - Uses secrets.token_bytes() for secure generation
            - Single-use only (validate and discard)
            - Should expire after 10 minutes

        Example:
            >>> state = oauth.generate_state()
            >>> len(state)
            43

        References:
            - OAuth 2.0 Section 10.12: https://tools.ietf.org/html/rfc6749#section-10.12
        """
        # Generate 32-byte random state (256 bits of entropy)
        state_bytes = secrets.token_bytes(32)
        state = base64.urlsafe_b64encode(state_bytes).decode("utf-8").rstrip("=")

        _LOGGER.debug("Generated state parameter (len=%d)", len(state))

        return state

    def validate_state(self, received_state: str, expected_state: str) -> bool:
        """Validate state parameter using constant-time comparison.

        Args:
            received_state: State parameter from OAuth callback
            expected_state: State parameter stored before redirect

        Returns:
            True if states match, False otherwise

        Security:
            Uses constant-time comparison to prevent timing attacks
        """
        # Use constant-time comparison to prevent timing attacks
        return hmac.compare_digest(received_state, expected_state)

    # =========================================================================
    # Authorization Request
    # =========================================================================

    async def get_authorization_url(
        self, flow_id: str, redirect_uri: str
    ) -> tuple[str, str, str]:
        """Generate OAuth authorization URL with PKCE.

        Constructs the authorization URL that users will be redirected to
        for authenticating with Amazon and granting permissions.

        Args:
            flow_id: Home Assistant config flow ID (for state storage)
            redirect_uri: Registered OAuth redirect URI

        Returns:
            Tuple of (authorization_url, code_verifier, state)
                - authorization_url: Complete URL for user redirect
                - code_verifier: PKCE verifier (store for token exchange)
                - state: State parameter (store for validation)

        Raises:
            ValueError: If flow_id or redirect_uri invalid

        URL Parameters:
            - client_id: Amazon client ID
            - response_type: "code" (authorization code flow)
            - scope: Required scopes (alexa::skills:account_linking)
            - redirect_uri: Registered callback URL
            - state: CSRF protection token
            - code_challenge: PKCE challenge
            - code_challenge_method: "S256" (SHA-256)

        Example:
            >>> url, verifier, state = await oauth.get_authorization_url(
            ...     "flow_123", "https://my.home-assistant.io/redirect/oauth"
            ... )
            >>> # Store verifier and state in flow context
            >>> # Redirect user to url

        References:
            - Amazon LWA: https://developer.amazon.com/docs/login-with-amazon/
        """
        if not flow_id:
            raise ValueError("flow_id cannot be empty")
        if not redirect_uri:
            raise ValueError("redirect_uri cannot be empty")

        # Generate PKCE pair and state
        code_verifier, code_challenge = self.generate_pkce_pair()
        state = self.generate_state()

        # Build authorization URL parameters
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": REQUIRED_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        # Construct complete authorization URL
        auth_url = f"{AMAZON_AUTH_URL}?{urlencode(params)}"

        _LOGGER.info(
            "Generated authorization URL for flow %s (client_id=%s..., state=%s...)",
            flow_id,
            self.client_id[:8],
            state[:8],
        )

        return auth_url, code_verifier, state

    # =========================================================================
    # Token Exchange
    # =========================================================================

    async def exchange_code(
        self,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> TokenResponse:
        """Exchange authorization code for access token.

        After user authorizes, Amazon redirects back with an authorization code.
        This method exchanges that code for access and refresh tokens.

        Args:
            code: Authorization code from OAuth callback
            code_verifier: Original PKCE verifier (before hashing)
            redirect_uri: Same redirect_uri used in authorization request

        Returns:
            TokenResponse with access_token, refresh_token, etc.

        Raises:
            AlexaInvalidCodeError: Invalid or expired authorization code
            AlexaInvalidGrantError: Invalid grant type or parameters
            AlexaTimeoutError: Request timeout
            AlexaNetworkError: Network errors
            AlexaOAuthError: Other OAuth errors

        Request Format:
            POST https://api.amazon.com/auth/o2/token
            Content-Type: application/x-www-form-urlencoded

            grant_type=authorization_code&
            code={code}&
            client_id={client_id}&
            client_secret={client_secret}&
            redirect_uri={redirect_uri}&
            code_verifier={code_verifier}

        Response Format (Success):
            {
                "access_token": "Atza|...",
                "refresh_token": "Atzr|...",
                "token_type": "Bearer",
                "expires_in": 3600
            }

        Response Format (Error):
            {
                "error": "invalid_grant",
                "error_description": "The authorization code is invalid"
            }

        Example:
            >>> try:
            ...     tokens = await oauth.exchange_code(code, verifier, redirect_uri)
            ...     print(f"Access token: {tokens.access_token[:10]}...")
            ... except AlexaInvalidCodeError:
            ...     print("Code invalid or expired, restart OAuth flow")

        References:
            - Amazon Token Exchange: https://developer.amazon.com/docs/login-with-amazon/
        """
        if not code:
            raise ValueError("code cannot be empty")
        if not code_verifier:
            raise ValueError("code_verifier cannot be empty")
        if not redirect_uri:
            raise ValueError("redirect_uri cannot be empty")

        # Build POST data
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }

        _LOGGER.info(
            "Exchanging authorization code for token (client_id=%s..., code=%s...)",
            self.client_id[:8],
            code[:8],
        )

        try:
            # Get aiohttp session
            session = async_get_clientsession(self.hass)

            # POST to token endpoint with timeout
            async with async_timeout.timeout(TOKEN_EXCHANGE_TIMEOUT_SECONDS):
                async with session.post(
                    AMAZON_TOKEN_URL,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ) as response:
                    response_data = await response.json()

                    # Handle error responses
                    if response.status != 200:
                        await self._handle_token_error(response, response_data)
                        return  # Unreachable, but added for code clarity

                    # Parse and validate success response
                    token_response = TokenResponse.from_dict(response_data)

                    _LOGGER.info(
                        "Successfully exchanged authorization code for token "
                        "(access_token=%s..., expires_in=%d)",
                        _redact_token(token_response.access_token),
                        token_response.expires_in,
                    )

                    return token_response

        except asyncio.TimeoutError as err:
            _LOGGER.error(
                "Timeout exchanging authorization code (timeout=%ds)",
                TOKEN_EXCHANGE_TIMEOUT_SECONDS,
            )
            raise AlexaTimeoutError(
                f"Token exchange timeout after {TOKEN_EXCHANGE_TIMEOUT_SECONDS}s"
            ) from err

        except aiohttp.ClientError as err:
            _LOGGER.error("Network error exchanging authorization code: %s", err)
            raise AlexaNetworkError(f"Network error during token exchange: {err}") from err

        except (KeyError, ValueError) as err:
            _LOGGER.error("Invalid token response format: %s", err)
            raise AlexaOAuthError(f"Invalid token response: {err}") from err

    # =========================================================================
    # Token Refresh
    # =========================================================================

    async def refresh_access_token(
        self,
        refresh_token: str,
    ) -> TokenResponse:
        """Refresh access token using refresh token.

        When access token expires (typically after 1 hour), use this method
        to obtain a new access token without requiring user interaction.

        Args:
            refresh_token: Refresh token from previous token response

        Returns:
            TokenResponse with new access_token (and possibly new refresh_token)

        Raises:
            AlexaInvalidGrantError: Refresh token expired or invalid
            AlexaTimeoutError: Request timeout
            AlexaNetworkError: Network errors
            AlexaOAuthError: Other OAuth errors

        Request Format:
            POST https://api.amazon.com/auth/o2/token
            Content-Type: application/x-www-form-urlencoded

            grant_type=refresh_token&
            refresh_token={refresh_token}&
            client_id={client_id}&
            client_secret={client_secret}

        Response Format:
            {
                "access_token": "Atza|...",  (always new)
                "refresh_token": "Atzr|...", (may be new or same)
                "token_type": "Bearer",
                "expires_in": 3600
            }

        Notes:
            - Amazon MAY issue a new refresh_token (always update storage)
            - Refresh tokens typically valid for 1 year
            - If refresh fails, trigger reauth flow

        Example:
            >>> try:
            ...     tokens = await oauth.refresh_access_token(old_refresh_token)
            ...     # Always save new tokens (refresh_token may have changed)
            ...     await save_tokens(tokens)
            ... except AlexaInvalidGrantError:
            ...     # Refresh token expired, need full reauth
            ...     await trigger_reauth_flow()

        References:
            - OAuth 2.0 Refresh: https://tools.ietf.org/html/rfc6749#section-6
        """
        if not refresh_token:
            raise ValueError("refresh_token cannot be empty")

        # Build POST data
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        _LOGGER.info(
            "Refreshing access token (client_id=%s..., refresh_token=%s...)",
            self.client_id[:8],
            _redact_token(refresh_token),
        )

        try:
            # Get aiohttp session
            session = async_get_clientsession(self.hass)

            # POST to token endpoint with timeout
            async with async_timeout.timeout(TOKEN_REFRESH_TIMEOUT_SECONDS):
                async with session.post(
                    AMAZON_TOKEN_URL,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ) as response:
                    response_data = await response.json()

                    # Handle error responses
                    if response.status != 200:
                        await self._handle_token_error(response, response_data)
                        return  # Unreachable, but added for code clarity

                    # Parse and validate success response
                    token_response = TokenResponse.from_dict(response_data)

                    _LOGGER.info(
                        "Successfully refreshed access token "
                        "(access_token=%s..., expires_in=%d)",
                        _redact_token(token_response.access_token),
                        token_response.expires_in,
                    )

                    return token_response

        except asyncio.TimeoutError as err:
            _LOGGER.error(
                "Timeout refreshing access token (timeout=%ds)",
                TOKEN_REFRESH_TIMEOUT_SECONDS,
            )
            raise AlexaTimeoutError(
                f"Token refresh timeout after {TOKEN_REFRESH_TIMEOUT_SECONDS}s"
            ) from err

        except aiohttp.ClientError as err:
            _LOGGER.error("Network error refreshing access token: %s", err)
            raise AlexaNetworkError(f"Network error during token refresh: {err}") from err

        except (KeyError, ValueError) as err:
            _LOGGER.error("Invalid token response format: %s", err)
            raise AlexaOAuthError(f"Invalid token response: {err}") from err

    # =========================================================================
    # Token Validation
    # =========================================================================

    async def validate_token(self, token: dict[str, Any]) -> bool:
        """Validate token structure and required fields.

        Args:
            token: Token dictionary to validate

        Returns:
            True if token valid, False otherwise

        Example:
            >>> token = {"access_token": "...", "refresh_token": "..."}
            >>> is_valid = await oauth.validate_token(token)
        """
        required_fields = ["access_token", "refresh_token", "token_type", "expires_in"]

        # Check all required fields present
        if not all(field in token for field in required_fields):
            _LOGGER.warning("Token missing required fields")
            return False

        # Validate token type
        if token.get("token_type") != "Bearer":
            _LOGGER.warning("Invalid token_type: %s", token.get("token_type"))
            return False

        # Validate access token prefix (Amazon LWA tokens start with Atza|)
        access_token = token.get("access_token", "")
        if not access_token.startswith("Atza|"):
            _LOGGER.warning("Invalid access_token format (missing Atza| prefix)")
            return False

        # Validate refresh token prefix (Amazon LWA refresh tokens start with Atzr|)
        refresh_token = token.get("refresh_token", "")
        if not refresh_token.startswith("Atzr|"):
            _LOGGER.warning("Invalid refresh_token format (missing Atzr| prefix)")
            return False

        return True

    # =========================================================================
    # Error Handling
    # =========================================================================

    async def _handle_token_error(
        self, response: aiohttp.ClientResponse, data: dict[str, Any]
    ) -> None:
        """Handle token endpoint error responses.

        Args:
            response: aiohttp response object
            data: Parsed JSON error response

        Raises:
            AlexaInvalidCodeError: Invalid authorization code
            AlexaInvalidGrantError: Invalid grant (expired refresh token)
            AlexaOAuthError: Other OAuth errors
        """
        error_code = data.get("error", "unknown_error")
        error_description = data.get("error_description", "No description provided")

        _LOGGER.error(
            "Token endpoint error (status=%d, error=%s, description=%s)",
            response.status,
            error_code,
            error_description,
        )

        # Map Amazon error codes to our exceptions
        if error_code == "invalid_grant":
            if "authorization code" in error_description.lower():
                raise AlexaInvalidCodeError(
                    f"Invalid authorization code: {error_description}"
                )
            else:
                raise AlexaInvalidGrantError(
                    f"Invalid grant: {error_description}"
                )
        elif error_code == "invalid_client":
            raise AlexaOAuthError(
                f"Invalid client credentials: {error_description}"
            )
        else:
            raise AlexaOAuthError(
                f"OAuth error ({error_code}): {error_description}"
            )


# =============================================================================
# Helper Functions
# =============================================================================


def _redact_token(token: str) -> str:
    """Redact token for logging.

    Shows first 4 and last 4 characters only.

    Args:
        token: Token to redact

    Returns:
        Redacted token string (e.g., "Atza...jXk")

    Example:
        >>> _redact_token("Atza|IwEBIExampleAccessToken")
        'Atza...oken'
    """
    if len(token) < 16:
        return "***"
    return f"{token[:4]}...{token[-4:]}"
