"""Tests for YAML migration functionality.

Comprehensive test coverage for yaml_migration.py module including:
- YAML detection
- YAML validation
- Migration process
- Device pairing preservation
- Rollback functionality
- Error scenarios
- Edge cases

Test Categories:
1. Detection Tests (test_detect_*)
2. Validation Tests (test_validate_*)
3. Migration Tests (test_migrate_*)
4. Device Preservation Tests (test_preserve_*)
5. Rollback Tests (test_rollback_*)
6. Error Handling Tests (test_error_*)
7. Edge Case Tests (test_edge_*)

Coverage Target: >90%
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import pytest
import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant

from custom_components.alexa.const import (
    DOMAIN,
    MIGRATION_STORAGE_KEY,
    YAML_CONFIG_SECTION,
)
from custom_components.alexa.exceptions import (
    AlexaDevicePairingError,
    AlexaMigrationError,
    AlexaMigrationInProgressError,
    AlexaMigrationRollbackError,
    AlexaYAMLInvalidError,
    AlexaYAMLNotFoundError,
)
from custom_components.alexa.yaml_migration import (
    MigrationResult,
    YAMLConfig,
    YAMLMigrator,
)


# Test Fixtures


@pytest.fixture
def mock_hass():
    """Create mock Home Assistant instance."""
    hass = MagicMock(spec=HomeAssistant)
    # Pre-create nested attributes before setting them (Python 3.12 spec strictness)
    hass.config = MagicMock()
    hass.config.path = MagicMock(return_value="/config/configuration.yaml")
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry = MagicMock()
    hass.config_entries.async_remove = AsyncMock()
    hass.config_entries.flow = MagicMock()
    hass.config_entries.flow.async_init = AsyncMock()
    hass.helpers = MagicMock()
    hass.async_add_executor_job = AsyncMock()
    return hass


@pytest.fixture
def valid_yaml_config():
    """Create valid YAMLConfig for testing."""
    return YAMLConfig(
        client_id="amzn1.application-oa2-client.1234567890abcdef",
        client_secret="abcdef1234567890abcdef1234567890abcdef1234567890",
        redirect_uri="https://example.com/auth/callback",
        region="na",
        raw_data={
            CONF_CLIENT_ID: "amzn1.application-oa2-client.1234567890abcdef",
            CONF_CLIENT_SECRET: "abcdef1234567890abcdef1234567890abcdef1234567890",
            "redirect_uri": "https://example.com/auth/callback",
            "region": "na",
        },
        file_path=Path("/config/configuration.yaml"),
    )


@pytest.fixture
def sample_yaml_content():
    """Create sample YAML content."""
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


@pytest.fixture
def migrator(mock_hass):
    """Create YAMLMigrator instance."""
    with patch("custom_components.alexa.yaml_migration.Store"):
        return YAMLMigrator(mock_hass)


# Detection Tests


@pytest.mark.asyncio
async def test_detect_yaml_config_success(migrator, mock_hass, sample_yaml_content):
    """Test successful YAML config detection."""
    with patch("builtins.open", mock_open(read_data=sample_yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                config = await migrator.async_detect_yaml_config()

    assert config is not None
    assert config.client_id == "amzn1.application-oa2-client.1234567890abcdef"
    assert len(config.client_secret) > 20
    assert config.region == "na"


@pytest.mark.asyncio
async def test_detect_yaml_config_no_file(migrator, mock_hass):
    """Test YAML detection when file doesn't exist."""
    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(AlexaYAMLNotFoundError):
            await migrator.async_detect_yaml_config()


@pytest.mark.asyncio
async def test_detect_yaml_config_no_alexa_section(migrator, mock_hass):
    """Test YAML detection when alexa section is missing."""
    yaml_content = """
homeassistant:
  name: Home
"""
    with patch("builtins.open", mock_open(read_data=yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            config = await migrator.async_detect_yaml_config()

    assert config is None


@pytest.mark.asyncio
async def test_detect_yaml_config_already_migrated(migrator, mock_hass, sample_yaml_content):
    """Test YAML detection when already migrated."""
    with patch("builtins.open", mock_open(read_data=sample_yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=True):
                config = await migrator.async_detect_yaml_config()

    assert config is None


@pytest.mark.asyncio
async def test_detect_yaml_config_malformed_yaml(migrator, mock_hass):
    """Test YAML detection with malformed YAML."""
    malformed_yaml = """
alexa:
  client_id: test
  invalid syntax here [
"""
    with patch("builtins.open", mock_open(read_data=malformed_yaml)):
        with patch("pathlib.Path.exists", return_value=True):
            with pytest.raises(AlexaYAMLInvalidError):
                await migrator.async_detect_yaml_config()


@pytest.mark.asyncio
async def test_detect_yaml_config_missing_credentials(migrator, mock_hass):
    """Test YAML detection with missing client_id or client_secret."""
    yaml_content = """
alexa:
  client_id: amzn1.application-oa2-client.1234567890abcdef
  # Missing client_secret
"""
    with patch("builtins.open", mock_open(read_data=yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                config = await migrator.async_detect_yaml_config()

    assert config is None


@pytest.mark.asyncio
async def test_detect_yaml_config_empty_file(migrator, mock_hass):
    """Test YAML detection with empty file."""
    with patch("builtins.open", mock_open(read_data="")):
        with patch("pathlib.Path.exists", return_value=True):
            config = await migrator.async_detect_yaml_config()

    assert config is None


# Validation Tests


@pytest.mark.asyncio
async def test_validate_yaml_config_success(migrator, valid_yaml_config):
    """Test successful YAML config validation."""
    result = await migrator.async_validate_yaml_config(valid_yaml_config)
    assert result is True


@pytest.mark.asyncio
async def test_validate_yaml_config_none(migrator):
    """Test validation with None config."""
    result = await migrator.async_validate_yaml_config(None)
    assert result is False


@pytest.mark.asyncio
async def test_validate_yaml_config_short_client_id(migrator):
    """Test validation with too short client_id."""
    config = YAMLConfig(
        client_id="short",  # Less than 10 chars
        client_secret="valid_client_secret_here_1234567890",
    )
    result = await migrator.async_validate_yaml_config(config)
    assert result is False


@pytest.mark.asyncio
async def test_validate_yaml_config_short_client_secret(migrator):
    """Test validation with too short client_secret."""
    config = YAMLConfig(
        client_id="amzn1.application-oa2-client.1234567890",
        client_secret="short",  # Less than 20 chars
    )
    result = await migrator.async_validate_yaml_config(config)
    assert result is False


@pytest.mark.asyncio
async def test_validate_yaml_config_invalid_region(migrator):
    """Test validation with invalid region."""
    config = YAMLConfig(
        client_id="amzn1.application-oa2-client.1234567890",
        client_secret="valid_client_secret_here_1234567890",
        region="invalid",  # Not na/eu/fe
    )
    result = await migrator.async_validate_yaml_config(config)
    assert result is False


@pytest.mark.asyncio
async def test_validate_yaml_config_valid_regions(migrator):
    """Test validation with all valid regions."""
    for region in ["na", "eu", "fe"]:
        config = YAMLConfig(
            client_id="amzn1.application-oa2-client.1234567890",
            client_secret="valid_client_secret_here_1234567890",
            region=region,
        )
        result = await migrator.async_validate_yaml_config(config)
        assert result is True


@pytest.mark.asyncio
async def test_validate_yaml_config_no_region(migrator):
    """Test validation with no region specified."""
    config = YAMLConfig(
        client_id="amzn1.application-oa2-client.1234567890",
        client_secret="valid_client_secret_here_1234567890",
        region=None,
    )
    result = await migrator.async_validate_yaml_config(config)
    assert result is True


# Migration Tests


@pytest.mark.asyncio
async def test_migrate_yaml_to_oauth2_success(migrator, valid_yaml_config, mock_hass):
    """Test successful migration from YAML to OAuth2."""
    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "test_entry_123"
    mock_entry.data = {CONF_CLIENT_ID: valid_yaml_config.client_id}

    with patch.object(migrator, "async_validate_yaml_config", return_value=True):
        with patch.object(migrator, "_create_backup", return_value=Path("/backup/path")):
            with patch.object(migrator, "_create_oauth_entry", return_value=mock_entry):
                with patch.object(migrator, "async_preserve_device_pairings", return_value=5):
                    with patch.object(migrator, "_mark_migrated"):
                        result = await migrator.async_migrate_yaml_to_oauth2(valid_yaml_config)

    assert result.success is True
    assert result.entry_id == "test_entry_123"
    assert result.devices_preserved == 5


@pytest.mark.asyncio
async def test_migrate_yaml_to_oauth2_validation_failure(migrator, valid_yaml_config, mock_hass):
    """Test migration failure due to validation."""
    with patch.object(migrator, "async_validate_yaml_config", return_value=False):
        result = await migrator.async_migrate_yaml_to_oauth2(valid_yaml_config)

    assert result.success is False
    assert "validation failed" in result.error.lower()


@pytest.mark.asyncio
async def test_migrate_yaml_to_oauth2_concurrent_migration(migrator, valid_yaml_config):
    """Test concurrent migration prevention."""
    # Lock the migrator
    await migrator._migration_lock.acquire()

    try:
        with pytest.raises(AlexaMigrationInProgressError):
            await migrator.async_migrate_yaml_to_oauth2(valid_yaml_config)
    finally:
        migrator._migration_lock.release()


@pytest.mark.asyncio
async def test_migrate_yaml_to_oauth2_rollback_on_error(migrator, valid_yaml_config, mock_hass):
    """Test automatic rollback on migration error."""
    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "test_entry_123"

    with patch.object(migrator, "async_validate_yaml_config", return_value=True):
        with patch.object(migrator, "_create_backup", return_value=Path("/backup/path")):
            with patch.object(migrator, "_create_oauth_entry", return_value=mock_entry):
                # Simulate error in device preservation
                with patch.object(
                    migrator,
                    "async_preserve_device_pairings",
                    side_effect=Exception("Device error"),
                ):
                    with patch.object(migrator, "_restore_from_backup"):
                        result = await migrator.async_migrate_yaml_to_oauth2(
                            valid_yaml_config
                        )

    assert result.success is False
    # Verify entry was removed (rollback)
    mock_hass.config_entries.async_remove.assert_called_once_with("test_entry_123")


@pytest.mark.asyncio
async def test_migrate_yaml_to_oauth2_backup_creation_failure(migrator, valid_yaml_config):
    """Test migration failure when backup creation fails."""
    with patch.object(migrator, "async_validate_yaml_config", return_value=True):
        with patch.object(
            migrator,
            "_create_backup",
            side_effect=AlexaMigrationError("Backup failed"),
        ):
            result = await migrator.async_migrate_yaml_to_oauth2(valid_yaml_config)

    assert result.success is False
    assert "backup failed" in result.error.lower()


# Device Preservation Tests


@pytest.mark.asyncio
async def test_preserve_device_pairings_success(migrator, mock_hass):
    """Test successful device pairing preservation."""
    # Mock device registry
    mock_device_registry = MagicMock()
    mock_device1 = MagicMock()
    mock_device1.id = "device_1"
    mock_device1.name = "Echo Dot"
    mock_device1.identifiers = {(DOMAIN, "echo_1")}

    mock_device2 = MagicMock()
    mock_device2.id = "device_2"
    mock_device2.name = "Echo Show"
    mock_device2.identifiers = {(DOMAIN, "echo_2")}

    mock_device_registry.devices.values.return_value = [mock_device1, mock_device2]
    mock_device_registry.async_update_device = MagicMock()

    mock_hass.helpers.device_registry.async_get_registry = AsyncMock(
        return_value=mock_device_registry
    )

    count = await migrator.async_preserve_device_pairings("entry_123")

    assert count == 2
    assert mock_device_registry.async_update_device.call_count == 2


@pytest.mark.asyncio
async def test_preserve_device_pairings_no_devices(migrator, mock_hass):
    """Test device preservation when no devices exist."""
    mock_device_registry = MagicMock()
    mock_device_registry.devices.values.return_value = []

    mock_hass.helpers.device_registry.async_get_registry = AsyncMock(
        return_value=mock_device_registry
    )

    count = await migrator.async_preserve_device_pairings("entry_123")

    assert count == 0


@pytest.mark.asyncio
async def test_preserve_device_pairings_error(migrator, mock_hass):
    """Test device preservation error handling."""
    mock_hass.helpers.device_registry.async_get_registry = AsyncMock(
        side_effect=Exception("Registry error")
    )

    with pytest.raises(AlexaDevicePairingError):
        await migrator.async_preserve_device_pairings("entry_123")


# Rollback Tests


@pytest.mark.asyncio
async def test_rollback_migration_success(migrator, mock_hass):
    """Test successful migration rollback."""
    backup_path = Path("/config/configuration.yaml.alexa_backup.1234567890")

    with patch("pathlib.Path.exists", return_value=True):
        with patch.object(migrator, "_restore_from_backup"):
            with patch.object(migrator._store, "async_save", new_callable=AsyncMock):
                result = await migrator.async_rollback_migration(backup_path)

    assert result is True


@pytest.mark.asyncio
async def test_rollback_migration_no_backup(migrator, mock_hass):
    """Test rollback failure when backup doesn't exist."""
    backup_path = Path("/config/nonexistent_backup")

    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(AlexaMigrationRollbackError):
            await migrator.async_rollback_migration(backup_path)


@pytest.mark.asyncio
async def test_rollback_migration_restore_error(migrator, mock_hass):
    """Test rollback failure when restore fails."""
    backup_path = Path("/config/configuration.yaml.alexa_backup.1234567890")

    with patch("pathlib.Path.exists", return_value=True):
        with patch.object(
            migrator,
            "_restore_from_backup",
            side_effect=Exception("Restore failed"),
        ):
            with pytest.raises(AlexaMigrationRollbackError):
                await migrator.async_rollback_migration(backup_path)


# Error Handling Tests


@pytest.mark.asyncio
async def test_is_already_migrated_true(migrator):
    """Test migration status check when already migrated."""
    migrator._store.async_load = AsyncMock(
        return_value={"migrated": True, "timestamp": 1234567890}
    )

    result = await migrator._is_already_migrated()
    assert result is True


@pytest.mark.asyncio
async def test_is_already_migrated_false(migrator):
    """Test migration status check when not migrated."""
    migrator._store.async_load = AsyncMock(return_value={"migrated": False})

    result = await migrator._is_already_migrated()
    assert result is False


@pytest.mark.asyncio
async def test_is_already_migrated_no_data(migrator):
    """Test migration status check with no stored data."""
    migrator._store.async_load = AsyncMock(return_value=None)

    result = await migrator._is_already_migrated()
    assert result is False


@pytest.mark.asyncio
async def test_mark_migrated(migrator, valid_yaml_config):
    """Test marking config as migrated."""
    migrator._store.async_save = AsyncMock()

    with patch("time.time", return_value=1234567890):
        await migrator._mark_migrated(valid_yaml_config)

    migrator._store.async_save.assert_called_once()
    call_args = migrator._store.async_save.call_args[0][0]
    assert call_args["migrated"] is True
    assert call_args["timestamp"] == 1234567890


@pytest.mark.asyncio
async def test_create_backup_success(migrator, mock_hass):
    """Test successful backup creation."""
    config_path = Path("/config/configuration.yaml")

    with patch("pathlib.Path.exists", return_value=True):
        with patch("time.time", return_value=1234567890):
            mock_hass.async_add_executor_job = AsyncMock()

            backup_path = await migrator._create_backup(config_path)

    assert ".alexa_backup.1234567890" in str(backup_path)


@pytest.mark.asyncio
async def test_create_backup_no_file(migrator, mock_hass):
    """Test backup creation when file doesn't exist."""
    config_path = Path("/config/nonexistent.yaml")

    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(AlexaMigrationError):
            await migrator._create_backup(config_path)


@pytest.mark.asyncio
async def test_create_backup_copy_error(migrator, mock_hass):
    """Test backup creation when copy fails."""
    config_path = Path("/config/configuration.yaml")

    with patch("pathlib.Path.exists", return_value=True):
        mock_hass.async_add_executor_job = AsyncMock(
            side_effect=Exception("Copy failed")
        )

        with pytest.raises(AlexaMigrationError):
            await migrator._create_backup(config_path)


@pytest.mark.asyncio
async def test_restore_from_backup_success(migrator, mock_hass):
    """Test successful restore from backup."""
    backup_path = Path("/config/configuration.yaml.alexa_backup.1234567890")
    config_path = Path("/config/configuration.yaml")

    with patch("pathlib.Path.exists", return_value=True):
        mock_hass.async_add_executor_job = AsyncMock()

        await migrator._restore_from_backup(backup_path, config_path)

    mock_hass.async_add_executor_job.assert_called_once()


@pytest.mark.asyncio
async def test_restore_from_backup_no_backup(migrator, mock_hass):
    """Test restore failure when backup doesn't exist."""
    backup_path = Path("/config/nonexistent_backup")
    config_path = Path("/config/configuration.yaml")

    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(AlexaMigrationRollbackError):
            await migrator._restore_from_backup(backup_path, config_path)


@pytest.mark.asyncio
async def test_restore_from_backup_none_config_path(migrator, mock_hass):
    """Test restore failure with None config path."""
    backup_path = Path("/config/backup")

    with patch("pathlib.Path.exists", return_value=True):
        with pytest.raises(AlexaMigrationRollbackError):
            await migrator._restore_from_backup(backup_path, None)


@pytest.mark.asyncio
async def test_create_oauth_entry_success(migrator, valid_yaml_config, mock_hass):
    """Test successful OAuth entry creation."""
    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "test_entry_123"

    mock_hass.config_entries.flow.async_init = AsyncMock(
        return_value={
            "type": "create_entry",
            "result": mock_entry,
        }
    )
    mock_hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)

    entry = await migrator._create_oauth_entry(valid_yaml_config)

    assert entry.entry_id == "test_entry_123"


@pytest.mark.asyncio
async def test_create_oauth_entry_flow_failure(migrator, valid_yaml_config, mock_hass):
    """Test OAuth entry creation when flow fails."""
    mock_hass.config_entries.flow.async_init = AsyncMock(
        return_value={
            "type": "abort",
            "reason": "invalid_auth",
        }
    )

    with pytest.raises(AlexaMigrationError):
        await migrator._create_oauth_entry(valid_yaml_config)


@pytest.mark.asyncio
async def test_create_oauth_entry_not_found(migrator, valid_yaml_config, mock_hass):
    """Test OAuth entry creation when entry not found after creation."""
    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "test_entry_123"

    mock_hass.config_entries.flow.async_init = AsyncMock(
        return_value={
            "type": "create_entry",
            "result": mock_entry,
        }
    )
    mock_hass.config_entries.async_get_entry = MagicMock(return_value=None)

    with pytest.raises(AlexaMigrationError):
        await migrator._create_oauth_entry(valid_yaml_config)


# Edge Case Tests


@pytest.mark.asyncio
async def test_detect_yaml_config_with_unicode(migrator, mock_hass):
    """Test YAML detection with unicode characters."""
    yaml_content = """
alexa:
  client_id: amzn1.application-oa2-client.1234567890abcdef
  client_secret: abcdef1234567890abcdef1234567890abcdef1234567890
  # Comment with unicode: 日本語, Français, Español
"""
    with patch("builtins.open", mock_open(read_data=yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                config = await migrator.async_detect_yaml_config()

    assert config is not None


@pytest.mark.asyncio
async def test_migrate_with_optional_fields(migrator, mock_hass):
    """Test migration with all optional fields present."""
    config = YAMLConfig(
        client_id="amzn1.application-oa2-client.1234567890abcdef",
        client_secret="abcdef1234567890abcdef1234567890abcdef1234567890",
        redirect_uri="https://example.com/callback",
        region="eu",
        file_path=Path("/config/configuration.yaml"),
    )

    mock_entry = MagicMock(spec=ConfigEntry)
    mock_entry.entry_id = "test_entry_123"
    mock_entry.data = {
        CONF_CLIENT_ID: config.client_id,
        "redirect_uri": config.redirect_uri,
        "region": config.region,
    }

    with patch.object(migrator, "async_validate_yaml_config", return_value=True):
        with patch.object(migrator, "_create_backup", return_value=Path("/backup")):
            with patch.object(migrator, "_create_oauth_entry", return_value=mock_entry):
                with patch.object(migrator, "async_preserve_device_pairings", return_value=0):
                    with patch.object(migrator, "_mark_migrated"):
                        result = await migrator.async_migrate_yaml_to_oauth2(config)

    assert result.success is True


@pytest.mark.asyncio
async def test_concurrent_detection_calls(migrator, mock_hass, sample_yaml_content):
    """Test multiple concurrent detection calls."""
    with patch("builtins.open", mock_open(read_data=sample_yaml_content)):
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(migrator, "_is_already_migrated", return_value=False):
                # Run multiple detections concurrently
                tasks = [
                    migrator.async_detect_yaml_config() for _ in range(5)
                ]
                # Add timeout to prevent hangs
                results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)

    # All should succeed
    assert all(r is not None for r in results)
    assert all(r.client_id == results[0].client_id for r in results)


@pytest.mark.asyncio
async def test_migration_with_very_long_credentials(migrator, mock_hass):
    """Test migration with very long client credentials."""
    config = YAMLConfig(
        client_id="amzn1.application-oa2-client." + ("a" * 200),
        client_secret="secret" + ("b" * 500),
    )

    # Should still validate
    result = await migrator.async_validate_yaml_config(config)
    assert result is True
