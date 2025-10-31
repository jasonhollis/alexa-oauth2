"""YAML migration for Alexa integration.

This module handles migration from YAML-based configuration to OAuth2-based
authentication for the Alexa integration. It provides:

- Automatic detection of YAML configuration
- Validation of YAML format and content
- Atomic migration to OAuth2 (all or nothing)
- Device pairing preservation (no re-setup needed)
- Rollback capability on failure
- Migration status tracking

Migration Flow:
    1. Detect YAML config in configuration.yaml
    2. Validate YAML format and required fields
    3. Extract OAuth2 credentials
    4. Create ConfigEntry with OAuth2 setup
    5. Preserve Alexa device pairings
    6. Mark YAML config as migrated
    7. Notify user of success

Atomic Operations:
    - All migration steps succeed or all rollback
    - No partial migrations left in inconsistent state
    - Backup created before any changes
    - Rollback restores original state

Example:
    >>> migrator = YAMLMigrator(hass)
    >>> yaml_config = await migrator.async_detect_yaml_config()
    >>> if yaml_config:
    ...     result = await migrator.async_migrate_yaml_to_oauth2(yaml_config)
    ...     if result.success:
    ...         print("Migration successful!")
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import yaml as yaml_util

from .const import (
    CONF_REDIRECT_URI,
    DOMAIN,
    MIGRATION_STORAGE_KEY,
    MIGRATION_STORAGE_VERSION,
    YAML_BACKUP_SUFFIX,
    YAML_CONFIG_FILENAME,
    YAML_CONFIG_SECTION,
    YAML_MIGRATION_MARKER,
)
from .exceptions import (
    AlexaDevicePairingError,
    AlexaMigrationError,
    AlexaMigrationInProgressError,
    AlexaMigrationRollbackError,
    AlexaYAMLInvalidError,
    AlexaYAMLNotFoundError,
)
from .token_manager import TokenManager

_LOGGER = logging.getLogger(__name__)


@dataclass
class YAMLConfig:
    """YAML configuration data.

    Attributes:
        client_id: OAuth2 client ID from Amazon
        client_secret: OAuth2 client secret from Amazon
        redirect_uri: OAuth2 redirect URI (optional)
        region: Amazon region (na/eu/fe, optional)
        raw_data: Complete raw YAML data
        file_path: Path to configuration.yaml file
    """

    client_id: str
    client_secret: str
    redirect_uri: str | None = None
    region: str | None = None
    raw_data: dict[str, Any] | None = None
    file_path: Path | None = None


@dataclass
class MigrationResult:
    """Migration operation result.

    Attributes:
        success: Whether migration succeeded
        entry_id: ConfigEntry ID if created
        error: Error message if failed
        devices_preserved: Number of device pairings preserved
        backup_path: Path to backup file
    """

    success: bool
    entry_id: str | None = None
    error: str | None = None
    devices_preserved: int = 0
    backup_path: Path | None = None


class YAMLMigrator:
    """Handles YAML to OAuth2 migration.

    This class provides atomic migration from YAML-based Alexa configuration
    to OAuth2-based authentication. All operations are atomic (all or nothing)
    with automatic rollback on failure.

    Features:
        - Automatic YAML detection in configuration.yaml
        - Validation of required fields and format
        - Atomic migration with rollback capability
        - Device pairing preservation (no re-setup)
        - Migration status tracking
        - Backup creation before changes

    Thread Safety:
        - Uses asyncio locks to prevent concurrent migrations
        - Safe for multiple HA instances (different config paths)

    Example:
        >>> migrator = YAMLMigrator(hass)
        >>> # Detect YAML config
        >>> yaml_config = await migrator.async_detect_yaml_config()
        >>> if yaml_config:
        ...     # Validate before migration
        ...     is_valid = await migrator.async_validate_yaml_config(yaml_config)
        ...     if is_valid:
        ...         # Migrate atomically
        ...         result = await migrator.async_migrate_yaml_to_oauth2(yaml_config)
        ...         if not result.success:
        ...             # Rollback automatically handled
        ...             _LOGGER.error(f"Migration failed: {result.error}")
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize YAML migrator.

        Args:
            hass: Home Assistant instance
        """
        self.hass = hass
        self._migration_lock = asyncio.Lock()
        self._store = Store(
            hass,
            MIGRATION_STORAGE_VERSION,
            MIGRATION_STORAGE_KEY,
        )

    async def async_detect_yaml_config(self) -> YAMLConfig | None:
        """Detect YAML configuration in configuration.yaml.

        Searches for Alexa configuration in the main configuration.yaml file.
        Looks for the 'alexa:' section with OAuth2 credentials.

        Returns:
            YAMLConfig object if found, None otherwise

        Raises:
            AlexaYAMLInvalidError: If YAML is malformed
            AlexaYAMLNotFoundError: If configuration.yaml not found

        Example:
            >>> config = await migrator.async_detect_yaml_config()
            >>> if config:
            ...     print(f"Found client_id: {config.client_id}")
        """
        config_path = Path(self.hass.config.path(YAML_CONFIG_FILENAME))

        if not config_path.exists():
            _LOGGER.debug("No configuration.yaml found at %s", config_path)
            raise AlexaYAMLNotFoundError(
                f"Configuration file not found: {config_path}"
            )

        try:
            # Use safe_load to prevent code execution
            with open(config_path, "r", encoding="utf-8") as config_file:
                config_data = yaml.safe_load(config_file)

            if not config_data:
                _LOGGER.debug("Empty configuration.yaml")
                return None

            # Look for alexa section
            alexa_config = config_data.get(YAML_CONFIG_SECTION)
            if not alexa_config:
                _LOGGER.debug("No alexa section in configuration.yaml")
                return None

            # Check if already migrated
            if await self._is_already_migrated():
                _LOGGER.info("YAML config already migrated, skipping detection")
                return None

            # Extract required fields
            client_id = alexa_config.get(CONF_CLIENT_ID)
            client_secret = alexa_config.get(CONF_CLIENT_SECRET)

            if not client_id or not client_secret:
                _LOGGER.debug(
                    "Alexa section found but missing credentials: "
                    "client_id=%s, client_secret=%s",
                    bool(client_id),
                    bool(client_secret),
                )
                return None

            # Optional fields
            redirect_uri = alexa_config.get(CONF_REDIRECT_URI)
            region = alexa_config.get("region")

            _LOGGER.info(
                "Detected YAML Alexa configuration with client_id: %s...",
                client_id[:10] if len(client_id) > 10 else client_id,
            )

            return YAMLConfig(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                region=region,
                raw_data=alexa_config,
                file_path=config_path,
            )

        except yaml.YAMLError as err:
            _LOGGER.error("Invalid YAML in configuration.yaml: %s", err)
            raise AlexaYAMLInvalidError(f"Malformed YAML: {err}") from err
        except OSError as err:
            _LOGGER.error("Cannot read configuration.yaml: %s", err)
            raise AlexaYAMLNotFoundError(f"Cannot read file: {err}") from err

    async def async_validate_yaml_config(self, yaml_config: YAMLConfig) -> bool:
        """Validate YAML configuration format and content.

        Checks:
            - Required fields present (client_id, client_secret)
            - Field formats valid (not empty, reasonable length)
            - Region valid if specified (na/eu/fe)
            - No obvious malformed data

        Args:
            yaml_config: YAMLConfig to validate

        Returns:
            True if valid, False otherwise

        Example:
            >>> config = await migrator.async_detect_yaml_config()
            >>> if await migrator.async_validate_yaml_config(config):
            ...     print("Config is valid")
        """
        if not yaml_config:
            _LOGGER.error("Cannot validate None config")
            return False

        # Validate client_id
        if not yaml_config.client_id or not isinstance(yaml_config.client_id, str):
            _LOGGER.error("Invalid client_id: must be non-empty string")
            return False

        if len(yaml_config.client_id) < 10:
            _LOGGER.error("Invalid client_id: too short (< 10 chars)")
            return False

        # Validate client_secret
        if not yaml_config.client_secret or not isinstance(
            yaml_config.client_secret, str
        ):
            _LOGGER.error("Invalid client_secret: must be non-empty string")
            return False

        if len(yaml_config.client_secret) < 20:
            _LOGGER.error("Invalid client_secret: too short (< 20 chars)")
            return False

        # Validate region if specified
        if yaml_config.region:
            valid_regions = ["na", "eu", "fe"]
            if yaml_config.region not in valid_regions:
                _LOGGER.error(
                    "Invalid region '%s': must be one of %s",
                    yaml_config.region,
                    valid_regions,
                )
                return False

        _LOGGER.info("YAML config validation passed")
        return True

    async def async_migrate_yaml_to_oauth2(
        self, yaml_config: YAMLConfig
    ) -> MigrationResult:
        """Migrate YAML configuration to OAuth2.

        Atomic migration process:
            1. Acquire migration lock (prevent concurrent migrations)
            2. Create backup of configuration.yaml
            3. Create OAuth2 ConfigEntry
            4. Preserve device pairings
            5. Mark YAML as migrated
            6. Commit changes

        If any step fails, automatic rollback restores original state.

        Args:
            yaml_config: Validated YAMLConfig to migrate

        Returns:
            MigrationResult with success status and details

        Raises:
            AlexaMigrationInProgressError: If migration already running
            AlexaMigrationError: If migration fails after max retries

        Example:
            >>> result = await migrator.async_migrate_yaml_to_oauth2(config)
            >>> if result.success:
            ...     print(f"Created entry: {result.entry_id}")
            ...     print(f"Preserved {result.devices_preserved} devices")
            ... else:
            ...     print(f"Migration failed: {result.error}")
        """
        if self._migration_lock.locked():
            raise AlexaMigrationInProgressError(
                "Migration already in progress, please wait"
            )

        async with self._migration_lock:
            backup_path = None
            created_entry = None

            try:
                _LOGGER.info("Starting YAML to OAuth2 migration")

                # Step 1: Validate config
                if not await self.async_validate_yaml_config(yaml_config):
                    return MigrationResult(
                        success=False,
                        error="YAML config validation failed",
                    )

                # Step 2: Create backup
                backup_path = await self._create_backup(yaml_config.file_path)
                _LOGGER.info("Created backup at %s", backup_path)

                # Step 3: Create OAuth2 ConfigEntry
                created_entry = await self._create_oauth_entry(yaml_config)
                _LOGGER.info("Created OAuth2 entry: %s", created_entry.entry_id)

                # Step 4: Preserve device pairings
                devices_preserved = await self.async_preserve_device_pairings(
                    created_entry.entry_id
                )
                _LOGGER.info("Preserved %d device pairings", devices_preserved)

                # Step 5: Mark as migrated
                await self._mark_migrated(yaml_config)
                _LOGGER.info("Marked YAML config as migrated")

                return MigrationResult(
                    success=True,
                    entry_id=created_entry.entry_id,
                    devices_preserved=devices_preserved,
                    backup_path=backup_path,
                )

            except Exception as err:
                _LOGGER.error("Migration failed, initiating rollback: %s", err)

                # Rollback: Remove created entry
                if created_entry:
                    try:
                        await self.hass.config_entries.async_remove(
                            created_entry.entry_id
                        )
                        _LOGGER.info("Rolled back: removed entry %s", created_entry.entry_id)
                    except Exception as rollback_err:
                        _LOGGER.error("Rollback failed: %s", rollback_err)

                # Rollback: Restore from backup
                if backup_path and backup_path.exists():
                    try:
                        await self._restore_from_backup(
                            backup_path, yaml_config.file_path
                        )
                        _LOGGER.info("Rolled back: restored from backup")
                    except Exception as rollback_err:
                        _LOGGER.error("Backup restore failed: %s", rollback_err)
                        raise AlexaMigrationRollbackError(
                            f"Rollback failed: {rollback_err}"
                        ) from rollback_err

                return MigrationResult(
                    success=False,
                    error=str(err),
                    backup_path=backup_path,
                )

    async def async_preserve_device_pairings(self, entry_id: str) -> int:
        """Preserve Alexa device pairings during migration.

        Alexa device pairings are stored in HA registry tied to the integration
        instance. During migration, we need to ensure device IDs are preserved
        so users don't need to re-pair their Alexa devices.

        Args:
            entry_id: ConfigEntry ID of new OAuth2 entry

        Returns:
            Number of device pairings preserved

        Raises:
            AlexaDevicePairingError: If preservation fails

        Example:
            >>> count = await migrator.async_preserve_device_pairings(entry_id)
            >>> print(f"Preserved {count} Alexa devices")
        """
        try:
            # Get device registry
            device_registry = await self.hass.helpers.device_registry.async_get_registry()

            # Find devices for Alexa domain
            # device.identifiers is a set of tuples like {(DOMAIN, device_id)}
            devices = [
                device
                for device in device_registry.devices.values()
                if any(identifier[0] == DOMAIN for identifier in device.identifiers if isinstance(identifier, tuple))
            ]

            preserved_count = 0

            for device in devices:
                # Update device to point to new config entry
                device_registry.async_update_device(
                    device.id,
                    add_config_entry_id=entry_id,
                )
                preserved_count += 1
                _LOGGER.debug(
                    "Preserved device %s (id: %s) for entry %s",
                    device.name,
                    device.id,
                    entry_id,
                )

            _LOGGER.info("Preserved %d Alexa device pairings", preserved_count)
            return preserved_count

        except Exception as err:
            _LOGGER.error("Failed to preserve device pairings: %s", err)
            raise AlexaDevicePairingError(
                f"Device pairing preservation failed: {err}"
            ) from err

    async def async_rollback_migration(self, backup_path: Path) -> bool:
        """Rollback migration by restoring from backup.

        Restores configuration.yaml from backup file and removes migration
        markers. Use this if migration succeeded but user wants to revert.

        Args:
            backup_path: Path to backup file

        Returns:
            True if rollback succeeded, False otherwise

        Raises:
            AlexaMigrationRollbackError: If rollback fails

        Example:
            >>> success = await migrator.async_rollback_migration(backup_path)
            >>> if success:
            ...     print("Rollback successful")
        """
        try:
            if not backup_path or not backup_path.exists():
                raise AlexaMigrationRollbackError(
                    f"Backup file not found: {backup_path}"
                )

            config_path = Path(self.hass.config.path(YAML_CONFIG_FILENAME))

            # Restore from backup
            await self._restore_from_backup(backup_path, config_path)

            # Remove migration marker
            await self._store.async_save({"migrated": False, "timestamp": None})

            _LOGGER.info("Migration rollback successful")
            return True

        except Exception as err:
            _LOGGER.error("Rollback failed: %s", err)
            raise AlexaMigrationRollbackError(f"Rollback failed: {err}") from err

    async def _is_already_migrated(self) -> bool:
        """Check if YAML config already migrated.

        Returns:
            True if already migrated, False otherwise
        """
        migration_state = await self._store.async_load()
        if not migration_state:
            return False

        return migration_state.get("migrated", False)

    async def _mark_migrated(self, yaml_config: YAMLConfig) -> None:
        """Mark YAML config as migrated.

        Args:
            yaml_config: YAMLConfig that was migrated
        """
        import time

        await self._store.async_save(
            {
                "migrated": True,
                "timestamp": time.time(),
                "config_path": str(yaml_config.file_path),
            }
        )

    async def _create_backup(self, config_path: Path | None) -> Path:
        """Create backup of configuration.yaml.

        Args:
            config_path: Path to configuration.yaml

        Returns:
            Path to backup file

        Raises:
            AlexaMigrationError: If backup creation fails
        """
        if not config_path or not config_path.exists():
            raise AlexaMigrationError(f"Config file not found: {config_path}")

        import shutil
        import time

        timestamp = int(time.time())
        backup_path = config_path.with_suffix(f"{YAML_BACKUP_SUFFIX}.{timestamp}")

        try:
            # Copy file
            await self.hass.async_add_executor_job(
                shutil.copy2, str(config_path), str(backup_path)
            )

            _LOGGER.info("Created backup: %s", backup_path)
            return backup_path

        except Exception as err:
            raise AlexaMigrationError(f"Backup creation failed: {err}") from err

    async def _restore_from_backup(
        self, backup_path: Path, config_path: Path | None
    ) -> None:
        """Restore configuration.yaml from backup.

        Args:
            backup_path: Path to backup file
            config_path: Path to configuration.yaml

        Raises:
            AlexaMigrationRollbackError: If restore fails
        """
        if not backup_path.exists():
            raise AlexaMigrationRollbackError(f"Backup not found: {backup_path}")

        if not config_path:
            raise AlexaMigrationRollbackError("Config path is None")

        import shutil

        try:
            await self.hass.async_add_executor_job(
                shutil.copy2, str(backup_path), str(config_path)
            )
            _LOGGER.info("Restored config from backup: %s", backup_path)

        except Exception as err:
            raise AlexaMigrationRollbackError(f"Restore failed: {err}") from err

    async def _create_oauth_entry(self, yaml_config: YAMLConfig) -> ConfigEntry:
        """Create OAuth2 ConfigEntry from YAML config.

        Args:
            yaml_config: YAMLConfig to convert

        Returns:
            Created ConfigEntry

        Raises:
            AlexaMigrationError: If entry creation fails
        """
        try:
            # Prepare entry data
            entry_data = {
                CONF_CLIENT_ID: yaml_config.client_id,
                CONF_CLIENT_SECRET: yaml_config.client_secret,
            }

            if yaml_config.redirect_uri:
                entry_data[CONF_REDIRECT_URI] = yaml_config.redirect_uri

            if yaml_config.region:
                entry_data["region"] = yaml_config.region

            # Create entry
            result = await self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "yaml_migration"},
                data=entry_data,
            )

            if result["type"] != "create_entry":
                raise AlexaMigrationError(
                    f"Failed to create entry: {result.get('reason', 'unknown')}"
                )

            entry_id = result["result"].entry_id
            entry = self.hass.config_entries.async_get_entry(entry_id)

            if not entry:
                raise AlexaMigrationError(f"Entry {entry_id} not found after creation")

            return entry

        except Exception as err:
            raise AlexaMigrationError(f"Entry creation failed: {err}") from err
