"""Alexa integration for Home Assistant.

This integration provides OAuth2-based connection to Amazon Alexa for:
- Smart home control (turn on/off, set brightness, etc.)
- State synchronization between HA and Alexa
- Proactive state reporting to Alexa
- Account linking for Alexa skills

Features:
- OAuth2 with PKCE (RFC 7636) for secure authentication
- Encrypted token storage with automatic refresh
- Background session management with token lifecycle
- Multi-account support
- Automatic reauth flow on token expiry

Setup Flow:
    1. User adds integration via UI
    2. OAuth2 flow authenticates with Amazon
    3. Tokens saved to encrypted storage
    4. SessionManager starts background refresh task
    5. Tokens auto-refreshed before expiry
    6. User notified if reauth needed

Example Config Entry:
    {
        "entry_id": "abc123",
        "domain": "alexa",
        "data": {
            "client_id": "amzn1.application-oa2-client.xxx",
            "client_secret": "xxx",
            "redirect_uri": "https://my.home-assistant.io/redirect/oauth"
        }
    }
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import DOMAIN
from .exceptions import AlexaTokenExpiredError, AlexaRefreshFailedError
from .oauth_manager import OAuthManager
from .session_manager import SessionManager
from .token_manager import TokenManager

_LOGGER = logging.getLogger(__name__)

# Platforms supported by this integration (Phase 1: None, future phases: light, switch, etc.)
PLATFORMS: list[Platform] = []


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up Alexa integration from YAML (deprecated).

    YAML configuration is deprecated for OAuth integrations.
    Users should use the UI-based config flow instead.

    Args:
        hass: Home Assistant instance
        config: YAML configuration (ignored)

    Returns:
        True (always succeeds, no-op)
    """
    _LOGGER.warning(
        "YAML configuration for Alexa is deprecated. "
        "Please remove it from configuration.yaml and use the UI to set up the integration."
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Alexa integration from a config entry.

    This is the main entry point for the integration. It:
    1. Initializes SessionManager (if not already initialized)
    2. Checks for pending tokens from OAuth flow
    3. Saves initial tokens if present
    4. Validates tokens or triggers reauth if invalid
    5. Starts platforms (if any defined)

    Args:
        hass: Home Assistant instance
        entry: ConfigEntry created by config flow

    Returns:
        True if setup successful

    Raises:
        ConfigEntryAuthFailed: Authentication failed (triggers reauth)
        ConfigEntryNotReady: Temporary failure (will retry)

    Flow:
        1. Initialize SessionManager (once per HA instance)
        2. Check if OAuth flow left pending tokens in hass.data
        3. If pending tokens exist, save them via TokenManager
        4. Validate tokens are present and valid
        5. If invalid, trigger reauth flow
        6. Forward setup to platforms (if any)

    Example:
        >>> # After OAuth flow completes:
        >>> # - config_flow creates ConfigEntry
        >>> # - async_setup_entry called
        >>> # - Tokens saved and validated
        >>> # - Background refresh task running
    """
    _LOGGER.info("Setting up Alexa integration (entry_id=%s)", entry.entry_id)

    # Initialize integration storage in hass.data
    hass.data.setdefault(DOMAIN, {})

    # Initialize SessionManager (once per HA instance)
    if "session_manager" not in hass.data[DOMAIN]:
        _LOGGER.info("Initializing SessionManager")
        session_manager = SessionManager(hass)
        await session_manager.async_setup()
        hass.data[DOMAIN]["session_manager"] = session_manager
    else:
        session_manager = hass.data[DOMAIN]["session_manager"]
        _LOGGER.debug("Using existing SessionManager")

    # Create TokenManager for this entry
    token_manager = TokenManager(hass, entry)

    # Check for pending tokens from OAuth flow
    client_id = entry.data[CONF_CLIENT_ID]
    pending_tokens_key = f"pending_tokens_{client_id}"

    if pending_tokens_key in hass.data[DOMAIN]:
        _LOGGER.info("Found pending tokens from OAuth flow, saving...")
        token_response = hass.data[DOMAIN].pop(pending_tokens_key)

        try:
            # Save initial tokens
            await token_manager.async_save_token(token_response)
            _LOGGER.info("Successfully saved initial tokens for entry %s", entry.entry_id)
        except Exception as err:
            _LOGGER.error("Failed to save initial tokens: %s", err)
            raise ConfigEntryNotReady("Failed to save initial tokens") from err

    # Validate tokens are present and valid
    try:
        # Attempt to get valid access token (will auto-refresh if needed)
        access_token = await session_manager.async_get_active_token(entry.entry_id)
        _LOGGER.info(
            "Token validation successful for entry %s (token=%s...)",
            entry.entry_id,
            access_token[:8],
        )
    except AlexaTokenExpiredError as err:
        # Token expired and refresh failed - trigger reauth
        _LOGGER.error("Token expired and refresh failed for entry %s: %s", entry.entry_id, err)
        raise ConfigEntryAuthFailed("Token expired, please re-authenticate") from err
    except Exception as err:
        # Other errors - temporary failure, retry later
        _LOGGER.error("Error validating tokens for entry %s: %s", entry.entry_id, err)
        raise ConfigEntryNotReady("Token validation failed, will retry") from err

    # Store entry data in hass.data for platforms to access
    hass.data[DOMAIN][entry.entry_id] = {
        "token_manager": token_manager,
        "session_manager": session_manager,
    }

    # Forward setup to platforms (if any defined)
    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("Alexa integration setup complete (entry_id=%s)", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Alexa config entry.

    This is called when the integration is being removed or disabled. It:
    1. Unloads platforms
    2. Revokes tokens with Amazon
    3. Cleans up entry data
    4. Tears down SessionManager if this is the last entry

    Args:
        hass: Home Assistant instance
        entry: ConfigEntry being removed

    Returns:
        True if unload successful

    Example:
        >>> # User removes Alexa integration:
        >>> # - async_unload_entry called
        >>> # - Tokens revoked with Amazon
        >>> # - Background task stopped (if last entry)
        >>> # - Resources cleaned up
    """
    _LOGGER.info("Unloading Alexa integration (entry_id=%s)", entry.entry_id)

    # Unload platforms
    if PLATFORMS:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if not unload_ok:
            _LOGGER.warning("Failed to unload platforms for entry %s", entry.entry_id)
            return False

    # Get entry data
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data:
        # Revoke tokens with Amazon (best effort)
        token_manager = entry_data.get("token_manager")
        if token_manager:
            try:
                await token_manager.async_revoke_token()
                _LOGGER.info("Successfully revoked tokens for entry %s", entry.entry_id)
            except Exception as err:
                _LOGGER.warning("Error revoking tokens for entry %s: %s", entry.entry_id, err)

        # Remove entry data
        hass.data[DOMAIN].pop(entry.entry_id)

    # Check if this is the last Alexa entry
    remaining_entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id
    ]

    if not remaining_entries:
        # Last entry - tear down SessionManager
        _LOGGER.info("Last Alexa entry removed, tearing down SessionManager")
        session_manager = hass.data[DOMAIN].get("session_manager")
        if session_manager:
            await session_manager.async_teardown()
            hass.data[DOMAIN].pop("session_manager", None)

    _LOGGER.info("Alexa integration unload complete (entry_id=%s)", entry.entry_id)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entry to new format.

    This handles version upgrades for ConfigEntry data schema.

    Args:
        hass: Home Assistant instance
        entry: ConfigEntry to migrate

    Returns:
        True if migration successful

    Example:
        Version 1 → Version 2:
        - Add new fields
        - Transform data format
        - Update entry.version
    """
    _LOGGER.info("Migrating Alexa config entry from version %s", entry.version)

    # Phase 1: No migrations needed (version 1 is current)
    if entry.version == 1:
        _LOGGER.debug("Config entry already at version 1, no migration needed")
        return True

    # Future migrations would go here
    # if entry.version == 1:
    #     # Migrate v1 → v2
    #     new_data = dict(entry.data)
    #     new_data["new_field"] = "default_value"
    #     hass.config_entries.async_update_entry(entry, data=new_data, version=2)
    #     _LOGGER.info("Migrated config entry to version 2")

    return True
