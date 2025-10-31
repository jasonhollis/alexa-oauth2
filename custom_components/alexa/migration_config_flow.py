"""Migration config flow for Alexa integration.

This module provides a user-friendly wizard UI for migrating from YAML-based
to OAuth2-based authentication. It extends the standard config flow with
migration-specific steps.

Migration Wizard Steps:
    1. Detect - Automatically detect YAML configuration
    2. Confirm - Ask user to confirm migration
    3. Migrate - Execute migration atomically
    4. Success - Show results and next steps

UI Integration:
    - Integrates with Home Assistant config flow UI
    - Shows migration progress
    - Handles errors gracefully
    - Provides rollback option

Example Flow:
    User triggers: Configuration -> Integrations -> Alexa -> Migrate from YAML

    Step 1 (detect):
        "We found YAML configuration with client ID: amzn1.application...
         Do you want to migrate to OAuth2?"

    Step 2 (confirm):
        "This will:
         - Create new OAuth2 entry
         - Preserve your Alexa devices
         - Backup configuration.yaml
         Continue?"

    Step 3 (migrate):
        "Migrating... (progress indicator)"

    Step 4 (success):
        "Migration successful!
         - Created entry: <entry_id>
         - Preserved 5 devices
         - Backup: /config/.alexa_backup.1234567890"
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from .exceptions import (
    AlexaMigrationError,
    AlexaYAMLInvalidError,
    AlexaYAMLNotFoundError,
)
from .yaml_migration import MigrationResult, YAMLConfig, YAMLMigrator

_LOGGER = logging.getLogger(__name__)


class MigrationFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle YAML to OAuth2 migration flow.

    This flow handler provides a user-friendly wizard for migrating
    from YAML-based to OAuth2-based authentication.

    Flow Steps:
        1. async_step_detect - Detect YAML config
        2. async_step_confirm - Confirm migration
        3. async_step_migrate - Execute migration
        4. async_step_success - Show results

    Example:
        # Initiated from UI
        flow = MigrationFlowHandler()
        result = await flow.async_step_detect()
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize migration flow handler."""
        self._yaml_config: YAMLConfig | None = None
        self._migration_result: MigrationResult | None = None
        self._migrator: YAMLMigrator | None = None

    @callback
    def _get_migrator(self) -> YAMLMigrator:
        """Get or create migrator instance.

        Returns:
            YAMLMigrator instance
        """
        if not self._migrator:
            self._migrator = YAMLMigrator(self.hass)
        return self._migrator

    async def async_step_detect(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Detect YAML configuration.

        First step: Automatically detect YAML configuration and show
        to user for confirmation.

        Args:
            user_input: User input (None for first call)

        Returns:
            FlowResult with next step or error
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            # User confirmed detection, proceed to confirm step
            return await self.async_step_confirm()

        try:
            migrator = self._get_migrator()
            self._yaml_config = await migrator.async_detect_yaml_config()

            if not self._yaml_config:
                errors["base"] = "no_yaml_config"
                _LOGGER.info("No YAML config detected")
                return self.async_abort(reason="no_yaml_config")

            # Validate detected config
            is_valid = await migrator.async_validate_yaml_config(self._yaml_config)
            if not is_valid:
                errors["base"] = "invalid_yaml_config"
                _LOGGER.error("YAML config validation failed")
                return self.async_abort(reason="invalid_yaml_config")

            # Show detected config to user
            _LOGGER.info(
                "Detected YAML config with client_id: %s...",
                self._yaml_config.client_id[:15],
            )

            return self.async_show_form(
                step_id="detect",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "client_id": self._yaml_config.client_id[:30] + "...",
                    "has_region": "Yes" if self._yaml_config.region else "No",
                    "region": self._yaml_config.region or "Not specified",
                },
            )

        except AlexaYAMLNotFoundError:
            _LOGGER.info("No configuration.yaml found")
            return self.async_abort(reason="no_config_file")

        except AlexaYAMLInvalidError as err:
            _LOGGER.error("Invalid YAML configuration: %s", err)
            return self.async_abort(reason="invalid_yaml")

        except Exception as err:
            _LOGGER.error("Unexpected error detecting YAML: %s", err)
            errors["base"] = "unknown"
            return self.async_abort(reason="unknown")

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm migration with user.

        Second step: Ask user to confirm migration and explain what
        will happen.

        Args:
            user_input: User input (None for first call)

        Returns:
            FlowResult with next step or error
        """
        if user_input is not None:
            # User confirmed, proceed to migration
            if user_input.get("confirm"):
                return await self.async_step_migrate()
            else:
                # User cancelled
                return self.async_abort(reason="user_cancelled")

        if not self._yaml_config:
            _LOGGER.error("No YAML config available for confirmation")
            return self.async_abort(reason="no_yaml_config")

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("confirm", default=True): bool,
                }
            ),
            description_placeholders={
                "client_id": self._yaml_config.client_id[:30] + "...",
                "backup_info": "A backup will be created at configuration.yaml.alexa_backup.<timestamp>",
            },
        )

    async def async_step_migrate(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Execute migration.

        Third step: Perform the actual migration atomically.

        Args:
            user_input: User input (not used, auto-proceed)

        Returns:
            FlowResult with success or error
        """
        if not self._yaml_config:
            _LOGGER.error("No YAML config available for migration")
            return self.async_abort(reason="no_yaml_config")

        try:
            _LOGGER.info("Starting migration...")

            migrator = self._get_migrator()
            self._migration_result = await migrator.async_migrate_yaml_to_oauth2(
                self._yaml_config
            )

            if not self._migration_result.success:
                _LOGGER.error(
                    "Migration failed: %s", self._migration_result.error
                )
                return self.async_abort(
                    reason="migration_failed",
                    description_placeholders={
                        "error": self._migration_result.error or "Unknown error"
                    },
                )

            # Migration successful, proceed to success step
            return await self.async_step_success()

        except AlexaMigrationError as err:
            _LOGGER.error("Migration error: %s", err)
            return self.async_abort(
                reason="migration_error",
                description_placeholders={"error": str(err)},
            )

        except Exception as err:
            _LOGGER.error("Unexpected migration error: %s", err)
            return self.async_abort(
                reason="unknown",
                description_placeholders={"error": str(err)},
            )

    async def async_step_success(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show migration success.

        Final step: Show success message with details.

        Args:
            user_input: User input (not used)

        Returns:
            FlowResult with create_entry
        """
        if not self._migration_result or not self._migration_result.success:
            _LOGGER.error("No successful migration result available")
            return self.async_abort(reason="no_result")

        _LOGGER.info(
            "Migration successful: entry_id=%s, devices=%d",
            self._migration_result.entry_id,
            self._migration_result.devices_preserved,
        )

        # Get the created entry
        entry = self.hass.config_entries.async_get_entry(
            self._migration_result.entry_id
        )

        if not entry:
            _LOGGER.error(
                "Entry %s not found after migration", self._migration_result.entry_id
            )
            return self.async_abort(reason="entry_not_found")

        # Return success with entry data
        return self.async_create_entry(
            title=f"Alexa (Migrated from YAML)",
            data=entry.data,
            description_placeholders={
                "entry_id": self._migration_result.entry_id,
                "devices_preserved": str(self._migration_result.devices_preserved),
                "backup_path": str(self._migration_result.backup_path)
                if self._migration_result.backup_path
                else "Not available",
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user-initiated migration.

        Entry point when user selects "Migrate from YAML" option.

        Args:
            user_input: User input (None for first call)

        Returns:
            FlowResult to start detection step
        """
        return await self.async_step_detect(user_input)

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> FlowResult:
        """Handle import from YAML.

        This step is triggered automatically when HA detects YAML config
        and suggests migration.

        Args:
            import_data: YAML data to import

        Returns:
            FlowResult to start migration flow
        """
        _LOGGER.info("YAML import triggered with data: %s", import_data.keys())

        # Store YAML data for migration
        if CONF_CLIENT_ID in import_data and CONF_CLIENT_SECRET in import_data:
            self._yaml_config = YAMLConfig(
                client_id=import_data[CONF_CLIENT_ID],
                client_secret=import_data[CONF_CLIENT_SECRET],
                redirect_uri=import_data.get("redirect_uri"),
                region=import_data.get("region"),
                raw_data=import_data,
            )

            # Proceed to confirmation
            return await self.async_step_confirm()

        # Invalid import data
        _LOGGER.error("Invalid YAML import data: missing credentials")
        return self.async_abort(reason="invalid_import_data")


class MigrationOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle migration options flow.

    Provides options for:
        - Rollback migration
        - Re-run migration
        - View migration status
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow.

        Args:
            config_entry: ConfigEntry for this integration instance
        """
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage migration options.

        Args:
            user_input: User input (None for first call)

        Returns:
            FlowResult with options form
        """
        if user_input is not None:
            if user_input.get("rollback"):
                return await self.async_step_rollback()

            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional("rollback", default=False): bool,
                }
            ),
        )

    async def async_step_rollback(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle migration rollback.

        Args:
            user_input: User input for confirmation

        Returns:
            FlowResult with rollback status
        """
        if user_input is not None:
            if user_input.get("confirm_rollback"):
                try:
                    migrator = YAMLMigrator(self.hass)

                    # Find backup file
                    # This is simplified - in production, track backup path in entry data
                    from pathlib import Path
                    config_path = Path(self.hass.config.path("configuration.yaml"))
                    backup_files = list(config_path.parent.glob("configuration.yaml.alexa_backup.*"))

                    if not backup_files:
                        return self.async_abort(reason="no_backup_found")

                    # Use most recent backup
                    backup_path = max(backup_files, key=lambda p: p.stat().st_mtime)

                    success = await migrator.async_rollback_migration(backup_path)

                    if success:
                        return self.async_create_entry(
                            title="",
                            data={"rollback_success": True},
                        )
                    else:
                        return self.async_abort(reason="rollback_failed")

                except Exception as err:
                    _LOGGER.error("Rollback error: %s", err)
                    return self.async_abort(
                        reason="rollback_error",
                        description_placeholders={"error": str(err)},
                    )

            return self.async_abort(reason="rollback_cancelled")

        return self.async_show_form(
            step_id="rollback",
            data_schema=vol.Schema(
                {
                    vol.Required("confirm_rollback", default=False): bool,
                }
            ),
            description_placeholders={
                "warning": "This will restore your YAML configuration and remove the OAuth2 entry. Your Alexa devices will need to be re-configured.",
            },
        )
