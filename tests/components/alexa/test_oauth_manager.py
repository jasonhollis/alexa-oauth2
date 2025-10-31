"""Unit tests for OAuth Manager."""

import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from homeassistant.components.alexa.const import (
    AMAZON_AUTH_URL,
    AMAZON_TOKEN_URL,
    REQUIRED_SCOPES,
)
from homeassistant.components.alexa.exceptions import (
    AlexaInvalidCodeError,
    AlexaInvalidGrantError,
    AlexaNetworkError,
    AlexaOAuthError,
    AlexaTimeoutError,
)
from homeassistant.components.alexa.oauth_manager import (
    OAuthManager,
    TokenResponse,
    _redact_token,
)
from homeassistant.core import HomeAssistant


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def oauth_manager(hass: HomeAssistant) -> OAuthManager:
    """Create OAuth manager instance."""
    return OAuthManager(
        hass=hass,
        client_id="amzn1.application-oa2-client.test123",
        client_secret="test_secret_12345",
    )


@pytest.fixture
def mock_token_response() -> dict:
    """Mock token response from Amazon."""
    return {
        "access_token": "Atza|IwEBIExampleAccessToken",
        "refresh_token": "Atzr|IwEBIExampleRefreshToken",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "alexa::skills:account_linking",
    }


# =============================================================================
# Test TokenResponse
# =============================================================================


def test_token_response_from_dict_success(mock_token_response):
    """Test TokenResponse.from_dict with valid data."""
    token = TokenResponse.from_dict(mock_token_response)

    assert token.access_token == "Atza|IwEBIExampleAccessToken"
    assert token.refresh_token == "Atzr|IwEBIExampleRefreshToken"
    assert token.token_type == "Bearer"
    assert token.expires_in == 3600
    assert token.scope == "alexa::skills:account_linking"


def test_token_response_from_dict_missing_fields():
    """Test TokenResponse.from_dict with missing required fields."""
    invalid_data = {
        "access_token": "Atza|test",
        # Missing refresh_token, token_type, expires_in
    }

    with pytest.raises(ValueError, match="Missing required fields"):
        TokenResponse.from_dict(invalid_data)


def test_token_response_from_dict_invalid_token_type():
    """Test TokenResponse.from_dict with invalid token_type."""
    invalid_data = {
        "access_token": "Atza|test",
        "refresh_token": "Atzr|test",
        "token_type": "InvalidType",  # Should be "Bearer"
        "expires_in": 3600,
    }

    with pytest.raises(ValueError, match="Invalid token_type"):
        TokenResponse.from_dict(invalid_data)


def test_token_response_from_dict_invalid_expires_in():
    """Test TokenResponse.from_dict with invalid expires_in."""
    invalid_data = {
        "access_token": "Atza|test",
        "refresh_token": "Atzr|test",
        "token_type": "Bearer",
        "expires_in": -100,  # Must be positive
    }

    with pytest.raises(ValueError, match="Invalid expires_in"):
        TokenResponse.from_dict(invalid_data)


def test_token_response_from_dict_no_scope(mock_token_response):
    """Test TokenResponse.from_dict without scope (optional field)."""
    del mock_token_response["scope"]
    token = TokenResponse.from_dict(mock_token_response)

    assert token.scope == ""


# =============================================================================
# Test PKCE Generation
# =============================================================================


def test_generate_pkce_pair(oauth_manager):
    """Test PKCE pair generation."""
    verifier, challenge = oauth_manager.generate_pkce_pair()

    # Verify lengths (32 bytes base64url = 43 chars without padding)
    assert len(verifier) == 43
    assert len(challenge) == 43

    # Verify verifier only contains URL-safe base64 characters
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in verifier)

    # Verify challenge is SHA-256 of verifier
    expected_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("utf-8")).digest()
        )
        .decode("utf-8")
        .rstrip("=")
    )
    assert challenge == expected_challenge


def test_generate_pkce_pair_uniqueness(oauth_manager):
    """Test that each PKCE pair is unique."""
    verifier1, challenge1 = oauth_manager.generate_pkce_pair()
    verifier2, challenge2 = oauth_manager.generate_pkce_pair()

    # Each call should produce unique values
    assert verifier1 != verifier2
    assert challenge1 != challenge2


def test_generate_pkce_pair_rfc_compliance(oauth_manager):
    """Test PKCE generation RFC 7636 compliance."""
    verifier, challenge = oauth_manager.generate_pkce_pair()

    # RFC 7636 requires verifier length 43-128 characters
    assert 43 <= len(verifier) <= 128

    # Verify challenge computation matches RFC 7636 S256 method
    computed_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    assert challenge == computed_challenge


# =============================================================================
# Test State Generation
# =============================================================================


def test_generate_state(oauth_manager):
    """Test state parameter generation."""
    state = oauth_manager.generate_state()

    # Verify length (32 bytes base64url = 43 chars without padding)
    assert len(state) == 43

    # Verify only URL-safe base64 characters
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in state)


def test_generate_state_uniqueness(oauth_manager):
    """Test that each state is unique."""
    state1 = oauth_manager.generate_state()
    state2 = oauth_manager.generate_state()

    # Each call should produce unique value
    assert state1 != state2


def test_validate_state_success(oauth_manager):
    """Test state validation with matching states."""
    state = oauth_manager.generate_state()

    # Validate with same state should succeed
    assert oauth_manager.validate_state(state, state) is True


def test_validate_state_failure(oauth_manager):
    """Test state validation with mismatched states."""
    state1 = oauth_manager.generate_state()
    state2 = oauth_manager.generate_state()

    # Validate with different states should fail
    assert oauth_manager.validate_state(state1, state2) is False


def test_validate_state_constant_time(oauth_manager):
    """Test that state validation uses constant-time comparison."""
    # This test verifies we use hmac.compare_digest, which is constant-time
    # We can't directly test timing, but we verify the behavior

    state = oauth_manager.generate_state()
    invalid_state = state[:-1] + ("a" if state[-1] != "a" else "b")

    # Should still return False consistently
    assert oauth_manager.validate_state(state, invalid_state) is False


# =============================================================================
# Test Authorization URL Generation
# =============================================================================


@pytest.mark.asyncio
async def test_get_authorization_url(oauth_manager):
    """Test authorization URL generation."""
    flow_id = "test_flow_123"
    redirect_uri = "https://my.home-assistant.io/redirect/oauth"

    auth_url, verifier, state = await oauth_manager.get_authorization_url(
        flow_id, redirect_uri
    )

    # Verify URL starts with correct endpoint
    assert auth_url.startswith(AMAZON_AUTH_URL)

    # Verify all required parameters present
    assert f"client_id={oauth_manager.client_id}" in auth_url
    assert "response_type=code" in auth_url
    assert f"scope={REQUIRED_SCOPES}" in auth_url
    assert f"redirect_uri={redirect_uri}" in auth_url
    assert f"state={state}" in auth_url
    assert "code_challenge=" in auth_url
    assert "code_challenge_method=S256" in auth_url

    # Verify verifier and state have correct lengths
    assert len(verifier) == 43
    assert len(state) == 43


@pytest.mark.asyncio
async def test_get_authorization_url_empty_flow_id(oauth_manager):
    """Test authorization URL generation with empty flow_id."""
    with pytest.raises(ValueError, match="flow_id cannot be empty"):
        await oauth_manager.get_authorization_url("", "https://example.com")


@pytest.mark.asyncio
async def test_get_authorization_url_empty_redirect_uri(oauth_manager):
    """Test authorization URL generation with empty redirect_uri."""
    with pytest.raises(ValueError, match="redirect_uri cannot be empty"):
        await oauth_manager.get_authorization_url("flow_123", "")


# =============================================================================
# Test Token Exchange
# =============================================================================


@pytest.mark.asyncio
async def test_exchange_code_success(
    oauth_manager, mock_token_response, hass
):
    """Test successful token exchange."""
    code = "test_auth_code_12345"
    verifier = "test_verifier_12345678901234567890123456789"
    redirect_uri = "https://my.home-assistant.io/redirect/oauth"

    # Mock aiohttp response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=mock_token_response)

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.post.return_value.__aenter__.return_value = (
            mock_response
        )

        token = await oauth_manager.exchange_code(code, verifier, redirect_uri)

        assert token.access_token == "Atza|IwEBIExampleAccessToken"
        assert token.refresh_token == "Atzr|IwEBIExampleRefreshToken"
        assert token.expires_in == 3600


@pytest.mark.asyncio
async def test_exchange_code_invalid_code(oauth_manager, hass):
    """Test token exchange with invalid authorization code."""
    code = "invalid_code"
    verifier = "test_verifier_12345678901234567890123456789"
    redirect_uri = "https://my.home-assistant.io/redirect/oauth"

    # Mock error response
    error_response = {
        "error": "invalid_grant",
        "error_description": "The authorization code is invalid",
    }
    mock_response = AsyncMock()
    mock_response.status = 400
    mock_response.json = AsyncMock(return_value=error_response)

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.post.return_value.__aenter__.return_value = (
            mock_response
        )

        with pytest.raises(AlexaInvalidCodeError, match="Invalid authorization code"):
            await oauth_manager.exchange_code(code, verifier, redirect_uri)


@pytest.mark.asyncio
async def test_exchange_code_invalid_client(oauth_manager, hass):
    """Test token exchange with invalid client credentials."""
    code = "test_code"
    verifier = "test_verifier_12345678901234567890123456789"
    redirect_uri = "https://my.home-assistant.io/redirect/oauth"

    # Mock error response
    error_response = {
        "error": "invalid_client",
        "error_description": "Invalid client credentials",
    }
    mock_response = AsyncMock()
    mock_response.status = 401
    mock_response.json = AsyncMock(return_value=error_response)

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.post.return_value.__aenter__.return_value = (
            mock_response
        )

        with pytest.raises(AlexaOAuthError, match="Invalid client credentials"):
            await oauth_manager.exchange_code(code, verifier, redirect_uri)


@pytest.mark.asyncio
async def test_exchange_code_timeout(oauth_manager, hass):
    """Test token exchange timeout."""
    code = "test_code"
    verifier = "test_verifier_12345678901234567890123456789"
    redirect_uri = "https://my.home-assistant.io/redirect/oauth"

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession"
    ) as mock_session:
        # Simulate timeout
        mock_session.return_value.post.return_value.__aenter__.side_effect = (
            aiohttp.ServerTimeoutError()
        )

        with pytest.raises(AlexaNetworkError):
            await oauth_manager.exchange_code(code, verifier, redirect_uri)


@pytest.mark.asyncio
async def test_exchange_code_network_error(oauth_manager, hass):
    """Test token exchange network error."""
    code = "test_code"
    verifier = "test_verifier_12345678901234567890123456789"
    redirect_uri = "https://my.home-assistant.io/redirect/oauth"

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession"
    ) as mock_session:
        # Simulate network error
        mock_session.return_value.post.return_value.__aenter__.side_effect = (
            aiohttp.ClientError("Network unreachable")
        )

        with pytest.raises(AlexaNetworkError, match="Network error"):
            await oauth_manager.exchange_code(code, verifier, redirect_uri)


@pytest.mark.asyncio
async def test_exchange_code_empty_code(oauth_manager):
    """Test token exchange with empty code."""
    with pytest.raises(ValueError, match="code cannot be empty"):
        await oauth_manager.exchange_code("", "verifier", "https://example.com")


@pytest.mark.asyncio
async def test_exchange_code_empty_verifier(oauth_manager):
    """Test token exchange with empty verifier."""
    with pytest.raises(ValueError, match="code_verifier cannot be empty"):
        await oauth_manager.exchange_code("code", "", "https://example.com")


@pytest.mark.asyncio
async def test_exchange_code_empty_redirect_uri(oauth_manager):
    """Test token exchange with empty redirect_uri."""
    with pytest.raises(ValueError, match="redirect_uri cannot be empty"):
        await oauth_manager.exchange_code("code", "verifier", "")


# =============================================================================
# Test Token Refresh
# =============================================================================


@pytest.mark.asyncio
async def test_refresh_access_token_success(
    oauth_manager, mock_token_response, hass
):
    """Test successful token refresh."""
    refresh_token = "Atzr|IwEBIExampleRefreshToken"

    # Mock aiohttp response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=mock_token_response)

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.post.return_value.__aenter__.return_value = (
            mock_response
        )

        token = await oauth_manager.refresh_access_token(refresh_token)

        assert token.access_token == "Atza|IwEBIExampleAccessToken"
        assert token.refresh_token == "Atzr|IwEBIExampleRefreshToken"
        assert token.expires_in == 3600


@pytest.mark.asyncio
async def test_refresh_access_token_invalid_grant(oauth_manager, hass):
    """Test token refresh with expired refresh token."""
    refresh_token = "Atzr|ExpiredRefreshToken"

    # Mock error response
    error_response = {
        "error": "invalid_grant",
        "error_description": "The refresh token is invalid or expired",
    }
    mock_response = AsyncMock()
    mock_response.status = 400
    mock_response.json = AsyncMock(return_value=error_response)

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.post.return_value.__aenter__.return_value = (
            mock_response
        )

        with pytest.raises(AlexaInvalidGrantError, match="Invalid grant"):
            await oauth_manager.refresh_access_token(refresh_token)


@pytest.mark.asyncio
async def test_refresh_access_token_empty_token(oauth_manager):
    """Test token refresh with empty refresh_token."""
    with pytest.raises(ValueError, match="refresh_token cannot be empty"):
        await oauth_manager.refresh_access_token("")


# =============================================================================
# Test Token Validation
# =============================================================================


@pytest.mark.asyncio
async def test_validate_token_success(oauth_manager, mock_token_response):
    """Test token validation with valid token."""
    is_valid = await oauth_manager.validate_token(mock_token_response)
    assert is_valid is True


@pytest.mark.asyncio
async def test_validate_token_missing_fields(oauth_manager):
    """Test token validation with missing fields."""
    invalid_token = {
        "access_token": "Atza|test",
        # Missing other required fields
    }
    is_valid = await oauth_manager.validate_token(invalid_token)
    assert is_valid is False


@pytest.mark.asyncio
async def test_validate_token_invalid_token_type(oauth_manager):
    """Test token validation with invalid token_type."""
    invalid_token = {
        "access_token": "Atza|test",
        "refresh_token": "Atzr|test",
        "token_type": "InvalidType",
        "expires_in": 3600,
    }
    is_valid = await oauth_manager.validate_token(invalid_token)
    assert is_valid is False


@pytest.mark.asyncio
async def test_validate_token_invalid_access_token_prefix(oauth_manager):
    """Test token validation with invalid access_token prefix."""
    invalid_token = {
        "access_token": "InvalidPrefix|test",
        "refresh_token": "Atzr|test",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    is_valid = await oauth_manager.validate_token(invalid_token)
    assert is_valid is False


@pytest.mark.asyncio
async def test_validate_token_invalid_refresh_token_prefix(oauth_manager):
    """Test token validation with invalid refresh_token prefix."""
    invalid_token = {
        "access_token": "Atza|test",
        "refresh_token": "InvalidPrefix|test",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    is_valid = await oauth_manager.validate_token(invalid_token)
    assert is_valid is False


# =============================================================================
# Test Helper Functions
# =============================================================================


def test_redact_token():
    """Test token redaction for logging."""
    token = "Atza|IwEBIExampleAccessToken"
    redacted = _redact_token(token)

    assert redacted == "Atza...oken"
    assert "IwEB" not in redacted
    assert "Example" not in redacted


def test_redact_token_short():
    """Test token redaction for short tokens."""
    token = "short"
    redacted = _redact_token(token)

    assert redacted == "***"
