"""Exceptions for the Alexa integration."""

from homeassistant.exceptions import HomeAssistantError


class AlexaException(HomeAssistantError):
    """Base exception for Alexa integration."""


class AlexaOAuthError(AlexaException):
    """Base OAuth error."""


class AlexaInvalidStateError(AlexaOAuthError):
    """Invalid state parameter (CSRF protection)."""


class AlexaInvalidCodeError(AlexaOAuthError):
    """Invalid or expired authorization code."""


class AlexaInvalidGrantError(AlexaOAuthError):
    """Invalid grant (expired refresh token or invalid parameters)."""


class AlexaNetworkError(AlexaOAuthError):
    """Network error during OAuth request."""


class AlexaTimeoutError(AlexaNetworkError):
    """Request timeout."""


class AlexaTokenError(AlexaException):
    """Base token management error."""


class AlexaTokenExpiredError(AlexaTokenError):
    """Access token expired."""


class AlexaRefreshFailedError(AlexaTokenError):
    """Token refresh failed."""


class AlexaTokenStorageError(AlexaTokenError):
    """Token storage error."""


class AlexaEncryptionError(AlexaTokenStorageError):
    """Token encryption/decryption error."""


# Phase 3: YAML Migration Exceptions


class AlexaMigrationError(AlexaException):
    """Base exception for migration errors."""


class AlexaYAMLNotFoundError(AlexaMigrationError):
    """YAML configuration not found."""


class AlexaYAMLInvalidError(AlexaMigrationError):
    """YAML configuration is invalid or malformed."""


class AlexaMigrationInProgressError(AlexaMigrationError):
    """Migration already in progress."""


class AlexaMigrationRollbackError(AlexaMigrationError):
    """Migration rollback failed."""


class AlexaDevicePairingError(AlexaMigrationError):
    """Device pairing preservation failed."""


# Phase 3: Advanced Reauth Exceptions


class AlexaReauthError(AlexaException):
    """Base exception for reauth errors."""


class AlexaRefreshTokenExpiredError(AlexaReauthError):
    """Refresh token expired and cannot be renewed."""


class AlexaAppRevokedError(AlexaReauthError):
    """User revoked app authorization on Amazon."""


class AlexaClientSecretRotatedError(AlexaReauthError):
    """Client secret was rotated and tokens are invalid."""


class AlexaRegionalEndpointError(AlexaReauthError):
    """Regional endpoint mismatch or change."""


class AlexaScopeChangedError(AlexaReauthError):
    """OAuth scopes changed, requiring reauthorization."""


class AlexaReauthMaxRetriesError(AlexaReauthError):
    """Maximum reauth retry attempts exceeded."""
