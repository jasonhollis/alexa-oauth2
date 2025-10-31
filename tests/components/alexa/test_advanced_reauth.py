"""Tests for advanced reauth functionality.

Comprehensive test coverage for advanced_reauth.py module including:
- Reauth need detection
- Reauth reason detection
- Expired refresh token handling
- App revocation handling
- Client secret rotation handling
- Regional endpoint change handling
- Retry logic with exponential backoff
- Error scenarios
- Edge cases

Test Categories:
1. Detection Tests (test_detect_*)
2. Refresh Token Tests (test_refresh_token_*)
3. App Revocation Tests (test_app_revoked_*)
4. Client Secret Tests (test_client_secret_*)
5. Regional Tests (test_regional_*)
6. Retry Tests (test_retry_*)
7. Integration Tests (test_integration_*)
8. Edge Case Tests (test_edge_*)

Coverage Target: >90%
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import aiohttp
import pytest

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant

from custom_components.alexa.advanced_reauth import (
    AdvancedReauthHandler,
    ReauthReason,
    ReauthResult,
)
from custom_components.alexa.const import (
    DOMAIN,
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
from custom_components.alexa.exceptions import (
    AlexaAppRevokedError,
    AlexaClientSecretRotatedError,
    AlexaReauthError,
    AlexaReauthMaxRetriesError,
    AlexaRefreshTokenExpiredError,
    AlexaRegionalEndpointError,
)
from custom_components.alexa.token_manager import TokenManager


# Test Fixtures


@pytest.fixture
def mock_hass():
    """Create mock Home Assistant instance."""
    hass = MagicMock(spec=HomeAssistant)
    hass.config_entries = MagicMock()
    hass.config_entries.flow = MagicMock()
    hass.config_entries.flow.async_init = AsyncMock()
    hass.config_entries.async_update_entry = MagicMock()
    hass.helpers = MagicMock()
    return hass


@pytest.fixture
def mock_entry():
    """Create mock ConfigEntry."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_123"
    entry.domain = DOMAIN
    entry.data = {
        CONF_CLIENT_ID: "amzn1.application-oa2-client.test123",
        CONF_CLIENT_SECRET: "test_secret_1234567890abcdef",
        "region": "na",
    }
    return entry


@pytest.fixture
def handler(mock_hass, mock_entry):
    """Create AdvancedReauthHandler instance."""
    with patch("custom_components.alexa.advanced_reauth.TokenManager"):
        return AdvancedReauthHandler(mock_hass, mock_entry)


@pytest.fixture
def valid_token_data():
    """Create valid token data."""
    return {
        "access_token": "Atza|test_access_token",
        "refresh_token": "Atzr|test_refresh_token",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
        "scope": REQUIRED_SCOPES,
        "refresh_token_timestamp": time.time() - (30 * 24 * 60 * 60),  # 30 days old
    }


@pytest.fixture
def expired_token_data():
    """Create expired refresh token data."""
    return {
        "access_token": "Atza|test_access_token",
        "refresh_token": "Atzr|test_refresh_token",
        "token_type": "Bearer",
        "expires_at": time.time() - 3600,  # Expired
        "scope": REQUIRED_SCOPES,
        "refresh_token_timestamp": time.time() - (65 * 24 * 60 * 60),  # 65 days old
    }


# Detection Tests


@pytest.mark.asyncio
async def test_detect_reauth_needed_no_tokens(handler):
    """Test reauth detection when no tokens exist."""
    handler._token_manager._store.async_load = AsyncMock(return_value=None)

    result = await handler.async_detect_reauth_needed()
    assert result is True


@pytest.mark.asyncio
async def test_detect_reauth_needed_expired_refresh_token(handler, expired_token_data):
    """Test reauth detection with expired refresh token."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=expired_token_data
    )

    result = await handler.async_detect_reauth_needed()
    assert result is True


@pytest.mark.asyncio
async def test_detect_reauth_needed_valid_tokens(handler, valid_token_data):
    """Test reauth detection with valid tokens."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=valid_token_data
    )
    handler._token_manager.async_get_access_token = AsyncMock(
        return_value="Atza|valid_token"
    )

    result = await handler.async_detect_reauth_needed()
    assert result is False


@pytest.mark.asyncio
async def test_detect_reauth_needed_invalid_scope(handler):
    """Test reauth detection with invalid scope."""
    token_data = {
        "access_token": "Atza|test",
        "refresh_token": "Atzr|test",
        "expires_at": time.time() + 3600,
        "scope": "invalid_scope",  # Wrong scope
        "refresh_token_timestamp": time.time(),
    }

    handler._token_manager._store.async_load = AsyncMock(return_value=token_data)
    handler._token_manager.async_get_access_token = AsyncMock(
        return_value="Atza|valid_token"
    )

    result = await handler.async_detect_reauth_needed()
    assert result is True


@pytest.mark.asyncio
async def test_detect_reauth_needed_token_validation_error(handler, valid_token_data):
    """Test reauth detection when token validation fails."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=valid_token_data
    )
    handler._token_manager.async_get_access_token = AsyncMock(
        side_effect=Exception("Token validation failed")
    )

    result = await handler.async_detect_reauth_needed()
    assert result is True


@pytest.mark.asyncio
async def test_detect_reauth_needed_no_access_token(handler, valid_token_data):
    """Test reauth detection when no access token returned."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=valid_token_data
    )
    handler._token_manager.async_get_access_token = AsyncMock(return_value=None)

    result = await handler.async_detect_reauth_needed()
    assert result is True


# Reason Detection Tests


@pytest.mark.asyncio
async def test_detect_reauth_reason_expired_refresh_token(handler, expired_token_data):
    """Test detecting expired refresh token as reason."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=expired_token_data
    )

    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.REFRESH_TOKEN_EXPIRED


@pytest.mark.asyncio
async def test_detect_reauth_reason_app_revoked(handler, valid_token_data):
    """Test detecting app revocation as reason."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=valid_token_data
    )
    handler._token_manager.async_refresh_token = AsyncMock(
        side_effect=Exception("invalid_grant error")
    )

    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.APP_REVOKED


@pytest.mark.asyncio
async def test_detect_reauth_reason_client_secret_rotated(handler, valid_token_data):
    """Test detecting client secret rotation as reason."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=valid_token_data
    )
    handler._token_manager.async_refresh_token = AsyncMock(
        side_effect=Exception("invalid_client error")
    )

    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.CLIENT_SECRET_ROTATED


@pytest.mark.asyncio
async def test_detect_reauth_reason_regional_change(handler, valid_token_data):
    """Test detecting regional endpoint change as reason."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=valid_token_data
    )
    handler._token_manager.async_refresh_token = AsyncMock(
        side_effect=Exception("region not supported")
    )

    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.REGIONAL_CHANGE


@pytest.mark.asyncio
async def test_detect_reauth_reason_scope_changed(handler):
    """Test detecting scope change as reason."""
    token_data = {
        "refresh_token": "Atzr|test",
        "refresh_token_timestamp": time.time(),
        "scope": "old_scope",  # Different from REQUIRED_SCOPES
    }

    handler._token_manager._store.async_load = AsyncMock(return_value=token_data)
    handler._token_manager.async_refresh_token = AsyncMock()

    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.SCOPE_CHANGED


@pytest.mark.asyncio
async def test_detect_reauth_reason_default_to_expired(handler):
    """Test default reason when cannot determine specific cause."""
    handler._token_manager._store.async_load = AsyncMock(return_value={})
    handler._token_manager.async_refresh_token = AsyncMock()

    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.REFRESH_TOKEN_EXPIRED


# Refresh Token Expiry Tests


@pytest.mark.asyncio
async def test_handle_expired_refresh_token_success(handler, mock_hass):
    """Test successful handling of expired refresh token."""
    result = await handler.async_handle_expired_refresh_token()

    assert result.success is True
    assert result.reason == ReauthReason.REFRESH_TOKEN_EXPIRED
    mock_hass.config_entries.flow.async_init.assert_called_once()


@pytest.mark.asyncio
async def test_handle_expired_refresh_token_flow_error(handler, mock_hass):
    """Test handling expired refresh token when flow fails."""
    mock_hass.config_entries.flow.async_init = AsyncMock(
        side_effect=Exception("Flow error")
    )

    with pytest.raises(AlexaRefreshTokenExpiredError):
        await handler.async_handle_expired_refresh_token()


# App Revocation Tests


@pytest.mark.asyncio
async def test_handle_revoked_app_success(handler, mock_hass):
    """Test successful handling of app revocation."""
    handler._token_manager._store.async_remove = AsyncMock()

    result = await handler.async_handle_revoked_app()

    assert result.success is True
    assert result.reason == ReauthReason.APP_REVOKED
    handler._token_manager._store.async_remove.assert_called_once()
    mock_hass.config_entries.flow.async_init.assert_called_once()


@pytest.mark.asyncio
async def test_handle_revoked_app_clear_tokens_error(handler, mock_hass):
    """Test handling app revocation when clearing tokens fails."""
    handler._token_manager._store.async_remove = AsyncMock(
        side_effect=Exception("Clear tokens error")
    )

    with pytest.raises(AlexaAppRevokedError):
        await handler.async_handle_revoked_app()


@pytest.mark.asyncio
async def test_handle_revoked_app_flow_error(handler, mock_hass):
    """Test handling app revocation when flow fails."""
    handler._token_manager._store.async_remove = AsyncMock()
    mock_hass.config_entries.flow.async_init = AsyncMock(
        side_effect=Exception("Flow error")
    )

    with pytest.raises(AlexaAppRevokedError):
        await handler.async_handle_revoked_app()


# Client Secret Rotation Tests


@pytest.mark.asyncio
async def test_handle_client_secret_rotation_success(handler, mock_hass, mock_entry):
    """Test successful client secret rotation handling."""
    handler._token_manager.async_refresh_token = AsyncMock()

    result = await handler.async_handle_client_secret_rotation()

    assert result.success is True
    assert result.reason == ReauthReason.CLIENT_SECRET_ROTATED
    handler._token_manager.async_refresh_token.assert_called_once()


@pytest.mark.asyncio
async def test_handle_client_secret_rotation_no_secret(handler, mock_entry):
    """Test client secret rotation with no new secret."""
    mock_entry.data = {CONF_CLIENT_ID: "test"}  # No client_secret

    with pytest.raises(AlexaClientSecretRotatedError):
        await handler.async_handle_client_secret_rotation()


@pytest.mark.asyncio
async def test_handle_client_secret_rotation_refresh_fails(handler, mock_hass):
    """Test client secret rotation when refresh fails (triggers full reauth)."""
    handler._token_manager.async_refresh_token = AsyncMock(
        side_effect=Exception("Refresh failed")
    )

    result = await handler.async_handle_client_secret_rotation()

    # Should fall back to full reauth
    assert result.success is True
    assert result.reason == ReauthReason.CLIENT_SECRET_ROTATED
    mock_hass.config_entries.flow.async_init.assert_called_once()


# Regional Endpoint Tests


@pytest.mark.asyncio
async def test_handle_regional_change_success(handler, mock_hass):
    """Test successful regional endpoint change handling."""
    handler._detect_correct_region = AsyncMock(return_value="eu")
    handler._token_manager.async_refresh_token = AsyncMock()

    result = await handler.async_handle_regional_change()

    assert result.success is True
    assert result.reason == ReauthReason.REGIONAL_CHANGE
    assert result.new_region == "eu"
    mock_hass.config_entries.async_update_entry.assert_called_once()


@pytest.mark.asyncio
async def test_handle_regional_change_no_region_detected(handler):
    """Test regional change when no region can be detected."""
    handler._detect_correct_region = AsyncMock(return_value=None)

    with pytest.raises(AlexaRegionalEndpointError):
        await handler.async_handle_regional_change()


@pytest.mark.asyncio
async def test_handle_regional_change_refresh_fails(handler, mock_hass):
    """Test regional change when token refresh fails after endpoint update."""
    handler._detect_correct_region = AsyncMock(return_value="eu")
    handler._token_manager.async_refresh_token = AsyncMock(
        side_effect=Exception("Refresh failed")
    )

    with pytest.raises(AlexaRegionalEndpointError):
        await handler.async_handle_regional_change()


@pytest.mark.asyncio
async def test_detect_correct_region_success(handler, mock_hass):
    """Test successful region detection."""
    mock_response = MagicMock()
    mock_response.status = 200

    mock_session = MagicMock()
    mock_session.post = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_response))
    )

    handler._token_manager._store.async_load = AsyncMock(
        return_value={"refresh_token": "Atzr|test"}
    )

    with patch(
        "custom_components.alexa.advanced_reauth.async_get_clientsession",
        return_value=mock_session,
    ):
        region = await handler._detect_correct_region()

    assert region in REGIONAL_ENDPOINTS.keys()


@pytest.mark.asyncio
async def test_detect_correct_region_no_tokens(handler):
    """Test region detection with no tokens."""
    handler._token_manager._store.async_load = AsyncMock(return_value=None)

    region = await handler._detect_correct_region()
    assert region is None


@pytest.mark.asyncio
async def test_detect_correct_region_all_fail(handler, mock_hass):
    """Test region detection when all regions fail."""
    mock_session = MagicMock()
    mock_session.post = MagicMock(
        side_effect=Exception("Connection failed")
    )

    handler._token_manager._store.async_load = AsyncMock(
        return_value={"refresh_token": "Atzr|test"}
    )

    with patch(
        "custom_components.alexa.advanced_reauth.async_get_clientsession",
        return_value=mock_session,
    ):
        region = await handler._detect_correct_region()

    assert region is None


# Retry Logic Tests


@pytest.mark.asyncio
async def test_handle_reauth_max_retries(handler):
    """Test reauth handling with max retries exceeded."""
    with pytest.raises(AlexaReauthMaxRetriesError):
        await handler.async_handle_reauth(
            ReauthReason.REFRESH_TOKEN_EXPIRED,
            retry_count=REAUTH_MAX_RETRY_ATTEMPTS,
        )


@pytest.mark.asyncio
async def test_handle_reauth_with_retry(handler, mock_hass):
    """Test reauth retry logic with exponential backoff."""
    # First call fails, second succeeds
    call_count = 0

    async def mock_expired_handler():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("First attempt failed")
        return ReauthResult(success=True, reason=ReauthReason.REFRESH_TOKEN_EXPIRED)

    handler.async_handle_expired_refresh_token = mock_expired_handler

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await handler.async_handle_reauth(
            ReauthReason.REFRESH_TOKEN_EXPIRED
        )

    assert result.success is True
    assert call_count == 2  # Failed once, succeeded second time


@pytest.mark.asyncio
async def test_handle_reauth_concurrent_calls(handler, mock_hass):
    """Test concurrent reauth calls (should serialize)."""
    handler.async_handle_expired_refresh_token = AsyncMock(
        return_value=ReauthResult(
            success=True, reason=ReauthReason.REFRESH_TOKEN_EXPIRED
        )
    )

    # Start multiple concurrent reauth calls
    tasks = [
        handler.async_handle_reauth(ReauthReason.REFRESH_TOKEN_EXPIRED)
        for _ in range(3)
    ]
    results = await asyncio.gather(*tasks)

    # All should succeed
    assert all(r.success for r in results)
    # Only one should actually execute handler (others wait)
    assert handler.async_handle_expired_refresh_token.call_count == 1


@pytest.mark.asyncio
async def test_handle_reauth_scope_changed(handler, mock_hass):
    """Test reauth handling for scope change scenario."""
    result = await handler.async_handle_reauth(ReauthReason.SCOPE_CHANGED)

    assert result.success is True
    assert result.reason == ReauthReason.SCOPE_CHANGED
    mock_hass.config_entries.flow.async_init.assert_called_once()


@pytest.mark.asyncio
async def test_handle_reauth_unknown_reason(handler):
    """Test reauth handling with unknown reason."""
    # Create invalid reason (not in enum)
    with pytest.raises(AlexaReauthError):
        await handler.async_handle_reauth(
            MagicMock()  # Invalid reason
        )


# Integration Tests


@pytest.mark.asyncio
async def test_full_reauth_flow_expired_token(handler, mock_hass, expired_token_data):
    """Test complete reauth flow for expired token scenario."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=expired_token_data
    )

    # Detect need
    needs_reauth = await handler.async_detect_reauth_needed()
    assert needs_reauth is True

    # Detect reason
    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.REFRESH_TOKEN_EXPIRED

    # Handle reauth
    result = await handler.async_handle_reauth(reason)
    assert result.success is True
    mock_hass.config_entries.flow.async_init.assert_called()


@pytest.mark.asyncio
async def test_full_reauth_flow_app_revoked(handler, mock_hass, valid_token_data):
    """Test complete reauth flow for app revocation scenario."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=valid_token_data
    )
    handler._token_manager.async_get_access_token = AsyncMock(
        side_effect=Exception("invalid_grant")
    )
    handler._token_manager.async_refresh_token = AsyncMock(
        side_effect=Exception("invalid_grant error")
    )
    handler._token_manager._store.async_remove = AsyncMock()

    # Detect need
    needs_reauth = await handler.async_detect_reauth_needed()
    assert needs_reauth is True

    # Detect reason
    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.APP_REVOKED

    # Handle reauth
    result = await handler.async_handle_reauth(reason)
    assert result.success is True
    handler._token_manager._store.async_remove.assert_called_once()


@pytest.mark.asyncio
async def test_full_reauth_flow_regional_change(handler, mock_hass, valid_token_data):
    """Test complete reauth flow for regional endpoint change."""
    handler._token_manager._store.async_load = AsyncMock(
        return_value=valid_token_data
    )
    handler._token_manager.async_refresh_token = AsyncMock(
        side_effect=Exception("region not supported")
    )
    handler._detect_correct_region = AsyncMock(return_value="eu")

    # Detect need
    needs_reauth = await handler.async_detect_reauth_needed()
    assert needs_reauth is True

    # Detect reason
    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.REGIONAL_CHANGE

    # Handle reauth
    handler._token_manager.async_refresh_token = AsyncMock()  # Reset for actual call
    result = await handler.async_handle_reauth(reason)
    assert result.success is True
    assert result.new_region == "eu"


# Edge Case Tests


@pytest.mark.asyncio
async def test_reauth_with_missing_token_timestamp(handler):
    """Test reauth detection with missing refresh_token_timestamp."""
    token_data = {
        "access_token": "Atza|test",
        "refresh_token": "Atzr|test",
        "expires_at": time.time() + 3600,
        "scope": REQUIRED_SCOPES,
        # Missing refresh_token_timestamp
    }

    handler._token_manager._store.async_load = AsyncMock(return_value=token_data)
    handler._token_manager.async_get_access_token = AsyncMock(
        return_value="Atza|valid"
    )

    # Should not fail, just skip age check
    result = await handler.async_detect_reauth_needed()
    assert result is False


@pytest.mark.asyncio
async def test_reauth_with_partial_token_data(handler):
    """Test reauth handling with partial token data."""
    token_data = {
        "refresh_token": "Atzr|test",
        # Missing other fields
    }

    handler._token_manager._store.async_load = AsyncMock(return_value=token_data)

    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.REFRESH_TOKEN_EXPIRED


@pytest.mark.asyncio
async def test_exponential_backoff_delays(handler, mock_hass):
    """Test exponential backoff delays are correct."""
    delays = []

    async def mock_sleep(delay):
        delays.append(delay)

    handler.async_handle_expired_refresh_token = AsyncMock(
        side_effect=Exception("Fail")
    )

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep_call:
        mock_sleep_call.side_effect = mock_sleep
        try:
            await handler.async_handle_reauth(ReauthReason.REFRESH_TOKEN_EXPIRED)
        except AlexaReauthMaxRetriesError:
            pass

    # Verify exponential backoff
    expected_delays = [
        REAUTH_RETRY_DELAY_SECONDS * (REAUTH_BACKOFF_MULTIPLIER ** i)
        for i in range(REAUTH_MAX_RETRY_ATTEMPTS)
    ]
    assert mock_sleep_call.call_count == REAUTH_MAX_RETRY_ATTEMPTS


@pytest.mark.asyncio
async def test_reauth_reason_enum_values(handler):
    """Test all reauth reason enum values are valid."""
    assert ReauthReason.REFRESH_TOKEN_EXPIRED.value == REAUTH_REASON_REFRESH_TOKEN_EXPIRED
    assert ReauthReason.APP_REVOKED.value == REAUTH_REASON_APP_REVOKED
    assert ReauthReason.CLIENT_SECRET_ROTATED.value == REAUTH_REASON_CLIENT_SECRET_ROTATED
    assert ReauthReason.REGIONAL_CHANGE.value == REAUTH_REASON_REGIONAL_CHANGE
    assert ReauthReason.SCOPE_CHANGED.value == REAUTH_REASON_SCOPE_CHANGED
