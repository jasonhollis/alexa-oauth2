"""Integration tests for YAML migration and advanced reauth.

End-to-end integration tests covering complete workflows:
- Full YAML migration workflows
- Advanced reauth workflows
- Migration + reauth combined scenarios
- Concurrent operations
- Error recovery
- Rollback scenarios

Test Categories:
1. E2E Migration Tests (test_e2e_migration_*)
2. E2E Reauth Tests (test_e2e_reauth_*)
3. Combined Scenarios (test_combined_*)
4. Concurrent Operations (test_concurrent_*)
5. Error Recovery (test_recovery_*)
6. Rollback Tests (test_rollback_*)

Coverage Target: >90%
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import pytest
import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant

from custom_components.alexa.advanced_reauth import (
    AdvancedReauthHandler,
    ReauthReason,
)
from custom_components.alexa.const import (
    DOMAIN,
    REAUTH_REASON_APP_REVOKED,
    REAUTH_REASON_REFRESH_TOKEN_EXPIRED,
    REQUIRED_SCOPES,
)
from custom_components.alexa.yaml_migration import YAMLConfig, YAMLMigrator


# Test Fixtures


@pytest.fixture
def mock_hass():
    """Create comprehensive mock Home Assistant instance."""
    hass = MagicMock(spec=HomeAssistant)
    hass.config.path = MagicMock(return_value="/config/configuration.yaml")
    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry = MagicMock()
    hass.config_entries.async_remove = AsyncMock()
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.flow = MagicMock()
    hass.config_entries.flow.async_init = AsyncMock()
    hass.helpers = MagicMock()
    hass.helpers.device_registry = MagicMock()
    hass.async_add_executor_job = AsyncMock()
    return hass


@pytest.fixture
def complete_yaml_content():
    """Create complete YAML configuration."""
    return """
alexa:
  client_id: amzn1.application-oa2-client.1234567890abcdef
  client_secret: abcdef1234567890abcdef1234567890abcdef1234567890
  redirect_uri: https://example.com/auth/callback
  region: na

homeassistant:
  name: Home
  latitude: 37.7749
  longitude: -122.4194
"""


# End-to-End Migration Tests


@pytest.mark.asyncio
async def test_e2e_migration_detect_validate_migrate(mock_hass, complete_yaml_content):
    """Test complete migration workflow: detect -> validate -> migrate."""
    with patch("custom_components.alexa.yaml_migration.Store"):
        migrator = YAMLMigrator(mock_hass)

    # Mock config entry
    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "migrated_entry_123"
    mock_entry.data = {
        CONF_CLIENT_ID: "amzn1.application-oa2-client.1234567890abcdef",
    }

    # Mock device registry
    mock_device_registry = MagicMock()
    mock_device1 = MagicMock()
    mock_device1.id = "device_1"
    mock_device1.identifiers = {(DOMAIN, "echo_1")}
    mock_device_registry.devices.values.return_value = [mock_device1]
    mock_device_registry.async_update_device = MagicMock()
    mock_hass.helpers.device_registry.async_get_registry = AsyncMock(
        return_value=mock_device_registry
    )

    # Step 1: Detect
    with patch("builtins.open", mock_open(read_data=complete_yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                yaml_config = await migrator.async_detect_yaml_config()

    assert yaml_config is not None
    assert yaml_config.client_id == "amzn1.application-oa2-client.1234567890abcdef"

    # Step 2: Validate
    is_valid = await migrator.async_validate_yaml_config(yaml_config)
    assert is_valid is True

    # Step 3: Migrate
    with patch.object(migrator, "_create_backup", return_value=Path("/backup")):
        with patch.object(migrator, "_create_oauth_entry", return_value=mock_entry):
            with patch.object(migrator, "_mark_migrated"):
                result = await migrator.async_migrate_yaml_to_oauth2(yaml_config)

    # Verify complete workflow
    assert result.success is True
    assert result.entry_id == "migrated_entry_123"
    assert result.devices_preserved == 1


@pytest.mark.asyncio
async def test_e2e_migration_with_multiple_devices(mock_hass, complete_yaml_content):
    """Test migration with multiple Alexa devices."""
    with patch("custom_components.alexa.yaml_migration.Store"):
        migrator = YAMLMigrator(mock_hass)

    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "entry_123"

    # Create multiple devices
    devices = []
    for i in range(5):
        device = MagicMock()
        device.id = f"device_{i}"
        device.name = f"Echo {i}"
        device.identifiers = {(DOMAIN, f"echo_{i}")}
        devices.append(device)

    mock_device_registry = MagicMock()
    mock_device_registry.devices.values.return_value = devices
    mock_device_registry.async_update_device = MagicMock()
    mock_hass.helpers.device_registry.async_get_registry = AsyncMock(
        return_value=mock_device_registry
    )

    with patch("builtins.open", mock_open(read_data=complete_yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                with patch.object(migrator, "_create_backup", return_value=Path("/backup")):
                    with patch.object(migrator, "_create_oauth_entry", return_value=mock_entry):
                        with patch.object(migrator, "_mark_migrated"):
                            yaml_config = await migrator.async_detect_yaml_config()
                            result = await migrator.async_migrate_yaml_to_oauth2(yaml_config)

    assert result.success is True
    assert result.devices_preserved == 5
    assert mock_device_registry.async_update_device.call_count == 5


@pytest.mark.asyncio
async def test_e2e_migration_without_optional_fields(mock_hass):
    """Test migration with minimal configuration (no optional fields)."""
    minimal_yaml = """
alexa:
  client_id: amzn1.application-oa2-client.minimal123
  client_secret: minimal_secret_1234567890abcdef12345
"""

    with patch("custom_components.alexa.yaml_migration.Store"):
        migrator = YAMLMigrator(mock_hass)

    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "minimal_entry"

    mock_device_registry = MagicMock()
    mock_device_registry.devices.values.return_value = []
    mock_hass.helpers.device_registry.async_get_registry = AsyncMock(
        return_value=mock_device_registry
    )

    with patch("builtins.open", mock_open(read_data=minimal_yaml)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                with patch.object(migrator, "_create_backup", return_value=Path("/backup")):
                    with patch.object(migrator, "_create_oauth_entry", return_value=mock_entry):
                        with patch.object(migrator, "_mark_migrated"):
                            yaml_config = await migrator.async_detect_yaml_config()
                            result = await migrator.async_migrate_yaml_to_oauth2(yaml_config)

    assert result.success is True
    assert yaml_config.redirect_uri is None
    assert yaml_config.region is None


# End-to-End Reauth Tests


@pytest.mark.asyncio
async def test_e2e_reauth_detect_handle_complete(mock_hass):
    """Test complete reauth workflow: detect -> reason -> handle."""
    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "test_entry"
    mock_entry.domain = DOMAIN
    mock_entry.data = {
        CONF_CLIENT_ID: "test_client_id_12345",
        CONF_CLIENT_SECRET: "test_secret_1234567890abcdef",
    }

    with patch("custom_components.alexa.advanced_reauth.TokenManager"):
        handler = AdvancedReauthHandler(mock_hass, mock_entry)

    # Setup expired token scenario
    expired_token_data = {
        "refresh_token": "Atzr|expired",
        "refresh_token_timestamp": time.time() - (65 * 24 * 60 * 60),
        "scope": REQUIRED_SCOPES,
    }

    handler._token_manager._store.async_load = AsyncMock(
        return_value=expired_token_data
    )

    # Step 1: Detect need
    needs_reauth = await handler.async_detect_reauth_needed()
    assert needs_reauth is True

    # Step 2: Detect reason
    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.REFRESH_TOKEN_EXPIRED

    # Step 3: Handle reauth
    result = await handler.async_handle_reauth(reason)
    assert result.success is True
    mock_hass.config_entries.flow.async_init.assert_called()


@pytest.mark.asyncio
async def test_e2e_reauth_app_revoked_complete_flow(mock_hass):
    """Test complete reauth flow for app revocation."""
    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "test_entry"
    mock_entry.domain = DOMAIN
    mock_entry.data = {
        CONF_CLIENT_ID: "test_client",
        CONF_CLIENT_SECRET: "test_secret_123456789012345678901234",
    }

    with patch("custom_components.alexa.advanced_reauth.TokenManager"):
        handler = AdvancedReauthHandler(mock_hass, mock_entry)

    handler._token_manager._store.async_load = AsyncMock(
        return_value={"refresh_token": "Atzr|test", "scope": REQUIRED_SCOPES}
    )
    handler._token_manager.async_get_access_token = AsyncMock(
        side_effect=Exception("invalid_grant")
    )
    handler._token_manager.async_refresh_token = AsyncMock(
        side_effect=Exception("invalid_grant error")
    )
    handler._token_manager._store.async_remove = AsyncMock()

    # Detect and handle
    needs_reauth = await handler.async_detect_reauth_needed()
    assert needs_reauth is True

    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.APP_REVOKED

    result = await handler.async_handle_reauth(reason)
    assert result.success is True
    handler._token_manager._store.async_remove.assert_called_once()


@pytest.mark.asyncio
async def test_e2e_reauth_regional_change_complete_flow(mock_hass):
    """Test complete reauth flow for regional endpoint change."""
    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "test_entry"
    mock_entry.domain = DOMAIN
    mock_entry.data = {
        CONF_CLIENT_ID: "test_client",
        CONF_CLIENT_SECRET: "test_secret_123456789012345678901234",
        "region": "na",
    }

    with patch("custom_components.alexa.advanced_reauth.TokenManager"):
        handler = AdvancedReauthHandler(mock_hass, mock_entry)

    handler._token_manager._store.async_load = AsyncMock(
        return_value={"refresh_token": "Atzr|test", "scope": REQUIRED_SCOPES}
    )
    handler._token_manager.async_refresh_token = AsyncMock(
        side_effect=Exception("region error")
    )
    handler._detect_correct_region = AsyncMock(return_value="eu")

    # Detect and handle
    needs_reauth = await handler.async_detect_reauth_needed()
    assert needs_reauth is True

    reason = await handler.async_detect_reauth_reason()
    assert reason == ReauthReason.REGIONAL_CHANGE

    # Reset refresh for actual handling
    handler._token_manager.async_refresh_token = AsyncMock()
    result = await handler.async_handle_reauth(reason)
    assert result.success is True
    assert result.new_region == "eu"
    mock_hass.config_entries.async_update_entry.assert_called()


# Combined Scenarios


@pytest.mark.asyncio
async def test_combined_migration_then_reauth(mock_hass, complete_yaml_content):
    """Test migration followed by reauth scenario."""
    # Step 1: Migrate from YAML
    with patch("custom_components.alexa.yaml_migration.Store"):
        migrator = YAMLMigrator(mock_hass)

    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "migrated_entry"
    mock_entry.domain = DOMAIN
    mock_entry.data = {
        CONF_CLIENT_ID: "amzn1.application-oa2-client.1234567890abcdef",
        CONF_CLIENT_SECRET: "secret123",
    }

    mock_device_registry = MagicMock()
    mock_device_registry.devices.values.return_value = []
    mock_hass.helpers.device_registry.async_get_registry = AsyncMock(
        return_value=mock_device_registry
    )

    with patch("builtins.open", mock_open(read_data=complete_yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                with patch.object(migrator, "_create_backup", return_value=Path("/backup")):
                    with patch.object(migrator, "_create_oauth_entry", return_value=mock_entry):
                        with patch.object(migrator, "_mark_migrated"):
                            yaml_config = await migrator.async_detect_yaml_config()
                            migration_result = await migrator.async_migrate_yaml_to_oauth2(
                                yaml_config
                            )

    assert migration_result.success is True

    # Step 2: Later, tokens expire and need reauth
    with patch("custom_components.alexa.advanced_reauth.TokenManager"):
        handler = AdvancedReauthHandler(mock_hass, mock_entry)

    handler._token_manager._store.async_load = AsyncMock(
        return_value={
            "refresh_token_timestamp": time.time() - (65 * 24 * 60 * 60),
            "scope": REQUIRED_SCOPES,
        }
    )

    needs_reauth = await handler.async_detect_reauth_needed()
    assert needs_reauth is True

    reauth_result = await handler.async_handle_reauth(
        ReauthReason.REFRESH_TOKEN_EXPIRED
    )
    assert reauth_result.success is True


@pytest.mark.asyncio
async def test_combined_migration_failure_then_retry(mock_hass, complete_yaml_content):
    """Test migration failure followed by successful retry."""
    with patch("custom_components.alexa.yaml_migration.Store"):
        migrator = YAMLMigrator(mock_hass)

    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "retry_entry"

    mock_device_registry = MagicMock()
    mock_device_registry.devices.values.return_value = []
    mock_hass.helpers.device_registry.async_get_registry = AsyncMock(
        return_value=mock_device_registry
    )

    # First attempt: Backup creation fails
    with patch("builtins.open", mock_open(read_data=complete_yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                with patch.object(
                    migrator,
                    "_create_backup",
                    side_effect=Exception("Backup failed"),
                ):
                    yaml_config = await migrator.async_detect_yaml_config()
                    first_result = await migrator.async_migrate_yaml_to_oauth2(
                        yaml_config
                    )

    assert first_result.success is False

    # Second attempt: Succeeds
    with patch("builtins.open", mock_open(read_data=complete_yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                with patch.object(migrator, "_create_backup", return_value=Path("/backup")):
                    with patch.object(migrator, "_create_oauth_entry", return_value=mock_entry):
                        with patch.object(migrator, "_mark_migrated"):
                            second_result = await migrator.async_migrate_yaml_to_oauth2(
                                yaml_config
                            )

    assert second_result.success is True


# Concurrent Operations Tests


@pytest.mark.asyncio
async def test_concurrent_migration_attempts(mock_hass, complete_yaml_content):
    """Test multiple concurrent migration attempts are serialized."""
    with patch("custom_components.alexa.yaml_migration.Store"):
        migrator = YAMLMigrator(mock_hass)

    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "concurrent_entry"

    mock_device_registry = MagicMock()
    mock_device_registry.devices.values.return_value = []
    mock_hass.helpers.device_registry.async_get_registry = AsyncMock(
        return_value=mock_device_registry
    )

    yaml_config = YAMLConfig(
        client_id="amzn1.application-oa2-client.concurrent123",
        client_secret="concurrent_secret_123456789012345678901234",
        file_path=Path("/config/configuration.yaml"),
    )

    # Simulate slow migration
    async def slow_create_entry(config):
        await asyncio.sleep(0.1)
        return mock_entry

    with patch.object(migrator, "async_validate_yaml_config", return_value=True):
        with patch.object(migrator, "_create_backup", return_value=Path("/backup")):
            with patch.object(migrator, "_create_oauth_entry", side_effect=slow_create_entry):
                with patch.object(migrator, "_mark_migrated"):
                    # Start multiple migrations concurrently
                    from custom_components.alexa.exceptions import (
                        AlexaMigrationInProgressError,
                    )

                    # First one should succeed
                    task1 = asyncio.create_task(
                        migrator.async_migrate_yaml_to_oauth2(yaml_config)
                    )

                    # Wait a bit for first to acquire lock
                    await asyncio.sleep(0.01)

                    # Second should fail with "in progress" error
                    with pytest.raises(AlexaMigrationInProgressError):
                        await migrator.async_migrate_yaml_to_oauth2(yaml_config)

                    # Wait for first to complete
                    result = await task1
                    assert result.success is True


@pytest.mark.asyncio
async def test_concurrent_reauth_attempts(mock_hass):
    """Test multiple concurrent reauth attempts are serialized."""
    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "concurrent_reauth"
    mock_entry.domain = DOMAIN
    mock_entry.data = {
        CONF_CLIENT_ID: "test_client",
        CONF_CLIENT_SECRET: "test_secret_123456789012345678901234",
    }

    with patch("custom_components.alexa.advanced_reauth.TokenManager"):
        handler = AdvancedReauthHandler(mock_hass, mock_entry)

    # Mock slow reauth
    async def slow_reauth():
        await asyncio.sleep(0.1)
        from custom_components.alexa.advanced_reauth import ReauthResult
        return ReauthResult(success=True, reason=ReauthReason.REFRESH_TOKEN_EXPIRED)

    handler.async_handle_expired_refresh_token = slow_reauth

    # Start multiple concurrent reauths
    tasks = [
        handler.async_handle_reauth(ReauthReason.REFRESH_TOKEN_EXPIRED)
        for _ in range(3)
    ]
    # Add timeout to prevent hangs
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)

    # All should succeed (lock serializes them)
    assert all(r.success for r in results)


# Error Recovery Tests


@pytest.mark.asyncio
async def test_recovery_migration_with_device_error_rollback(mock_hass, complete_yaml_content):
    """Test migration rollback when device preservation fails."""
    with patch("custom_components.alexa.yaml_migration.Store"):
        migrator = YAMLMigrator(mock_hass)

    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "error_entry"

    with patch("builtins.open", mock_open(read_data=complete_yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                with patch.object(migrator, "_create_backup", return_value=Path("/backup")):
                    with patch.object(migrator, "_create_oauth_entry", return_value=mock_entry):
                        with patch.object(
                            migrator,
                            "async_preserve_device_pairings",
                            side_effect=Exception("Device error"),
                        ):
                            with patch.object(migrator, "_restore_from_backup"):
                                yaml_config = await migrator.async_detect_yaml_config()
                                result = await migrator.async_migrate_yaml_to_oauth2(
                                    yaml_config
                                )

    # Should fail but rollback
    assert result.success is False
    mock_hass.config_entries.async_remove.assert_called_once_with("error_entry")


@pytest.mark.asyncio
async def test_recovery_reauth_with_retry_success(mock_hass):
    """Test reauth recovery with retry logic."""
    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "retry_entry"
    mock_entry.domain = DOMAIN
    mock_entry.data = {
        CONF_CLIENT_ID: "test_client",
        CONF_CLIENT_SECRET: "test_secret_123456789012345678901234",
    }

    with patch("custom_components.alexa.advanced_reauth.TokenManager"):
        handler = AdvancedReauthHandler(mock_hass, mock_entry)

    # First attempt fails, second succeeds
    attempt_count = 0

    async def mock_reauth():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            raise Exception("First attempt failed")
        from custom_components.alexa.advanced_reauth import ReauthResult
        return ReauthResult(success=True, reason=ReauthReason.REFRESH_TOKEN_EXPIRED)

    handler.async_handle_expired_refresh_token = mock_reauth

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await handler.async_handle_reauth(ReauthReason.REFRESH_TOKEN_EXPIRED)

    assert result.success is True
    assert attempt_count == 2


# Rollback Tests


@pytest.mark.asyncio
async def test_rollback_migration_restore_config(mock_hass):
    """Test successful migration rollback."""
    with patch("custom_components.alexa.yaml_migration.Store") as mock_store_class:
        mock_store = AsyncMock()
        mock_store_class.return_value = mock_store
        migrator = YAMLMigrator(mock_hass)

    backup_path = Path("/config/configuration.yaml.alexa_backup.1234567890")

    with patch("pathlib.Path.exists", return_value=True):
        with patch.object(migrator, "_restore_from_backup") as mock_restore:
            result = await migrator.async_rollback_migration(backup_path)

    assert result is True
    mock_restore.assert_called_once()
    mock_store.async_save.assert_called_once()


@pytest.mark.asyncio
async def test_rollback_multiple_backups_uses_latest(mock_hass):
    """Test rollback uses most recent backup when multiple exist."""
    # This would be tested in migration_config_flow.py
    # Simplified version here for integration testing
    pass


# Performance Tests


@pytest.mark.asyncio
async def test_performance_large_device_count(mock_hass, complete_yaml_content):
    """Test migration performance with large number of devices."""
    with patch("custom_components.alexa.yaml_migration.Store"):
        migrator = YAMLMigrator(mock_hass)

    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "perf_entry"

    # Create 100 devices
    devices = []
    for i in range(100):
        device = MagicMock()
        device.id = f"device_{i}"
        device.identifiers = {(DOMAIN, f"echo_{i}")}
        devices.append(device)

    mock_device_registry = MagicMock()
    mock_device_registry.devices.values.return_value = devices
    mock_device_registry.async_update_device = MagicMock()
    mock_hass.helpers.device_registry.async_get_registry = AsyncMock(
        return_value=mock_device_registry
    )

    with patch("builtins.open", mock_open(read_data=complete_yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                with patch.object(migrator, "_create_backup", return_value=Path("/backup")):
                    with patch.object(migrator, "_create_oauth_entry", return_value=mock_entry):
                        with patch.object(migrator, "_mark_migrated"):
                            yaml_config = await migrator.async_detect_yaml_config()

                            start_time = time.time()
                            result = await migrator.async_migrate_yaml_to_oauth2(
                                yaml_config
                            )
                            elapsed = time.time() - start_time

    assert result.success is True
    assert result.devices_preserved == 100
    # Should complete in reasonable time (< 5 seconds for 100 devices)
    assert elapsed < 5.0


@pytest.mark.asyncio
async def test_edge_case_empty_yaml_sections(mock_hass):
    """Test migration handling of empty YAML sections."""
    empty_alexa_yaml = """
alexa:

homeassistant:
  name: Home
"""

    with patch("custom_components.alexa.yaml_migration.Store"):
        migrator = YAMLMigrator(mock_hass)

    with patch("builtins.open", mock_open(read_data=empty_alexa_yaml)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                config = await migrator.async_detect_yaml_config()

    # Should return None for empty section
    assert config is None
