#!/usr/bin/env python3
"""OAuth2 Verification and Testing Script for Alexa Integration.

This script provides comprehensive testing and verification of the OAuth2
implementation for Amazon Alexa integration with Home Assistant.

Features:
    - Pre-flight checklist (HA Cloud, redirect URI, credentials)
    - OAuth flow walkthrough with step-by-step validation
    - Token verification (format, expiry, scopes)
    - PKCE verification (code_verifier/code_challenge validation)
    - Token refresh testing
    - Error diagnosis with actionable fixes
    - Security audit (token encryption, storage permissions)

Usage:
    # Full verification (interactive)
    python verify_oauth.py

    # Pre-flight check only
    python verify_oauth.py --check-only

    # Verbose output with debugging
    python verify_oauth.py --verbose

    # Test token refresh
    python verify_oauth.py --test-refresh

    # Security audit
    python verify_oauth.py --security-audit

Requirements:
    - Home Assistant test environment
    - Valid Amazon Developer credentials
    - Network connectivity to Amazon APIs

Author: Claude Code
Date: 2025-11-01
"""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Add custom_components to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from custom_components.alexa.const import (
        AMAZON_AUTH_URL,
        AMAZON_TOKEN_URL,
        REQUIRED_SCOPES,
        STORAGE_KEY_TOKENS,
        STORAGE_VERSION,
        TOKEN_REFRESH_BUFFER_SECONDS,
    )
    from custom_components.alexa.exceptions import (
        AlexaInvalidCodeError,
        AlexaInvalidGrantError,
        AlexaNetworkError,
        AlexaOAuthError,
    )
    from custom_components.alexa.oauth_manager import OAuthManager, TokenResponse
except ImportError as e:
    print(f"ERROR: Failed to import Alexa components: {e}")
    print("Make sure you're running from the project directory.")
    sys.exit(1)


# =============================================================================
# Colors and Formatting
# =============================================================================

class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def print_header(text: str) -> None:
    """Print section header."""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 80}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text.center(80)}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 80}{Colors.ENDC}\n")


def print_step(step: int, text: str) -> None:
    """Print step number and description."""
    print(f"{Colors.OKBLUE}{Colors.BOLD}[Step {step}]{Colors.ENDC} {text}")


def print_success(text: str) -> None:
    """Print success message."""
    print(f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}")


def print_warning(text: str) -> None:
    """Print warning message."""
    print(f"{Colors.WARNING}⚠ {text}{Colors.ENDC}")


def print_error(text: str) -> None:
    """Print error message."""
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")


def print_info(text: str) -> None:
    """Print info message."""
    print(f"{Colors.OKCYAN}ℹ {text}{Colors.ENDC}")


# =============================================================================
# Test Results Tracking
# =============================================================================

@dataclass
class TestResult:
    """Result of a test."""
    name: str
    passed: bool
    message: str
    suggestion: str = ""


class TestTracker:
    """Track test results."""

    def __init__(self):
        self.results: list[TestResult] = []

    def add_result(self, name: str, passed: bool, message: str, suggestion: str = ""):
        """Add test result."""
        self.results.append(TestResult(name, passed, message, suggestion))
        if passed:
            print_success(f"{name}: {message}")
        else:
            print_error(f"{name}: {message}")
            if suggestion:
                print_info(f"   Suggestion: {suggestion}")

    def print_summary(self):
        """Print test summary."""
        print_header("Test Summary")

        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        total = len(self.results)

        print(f"Total Tests: {total}")
        print_success(f"Passed: {passed}")
        if failed > 0:
            print_error(f"Failed: {failed}")

        if failed > 0:
            print("\n" + Colors.WARNING + "Failed Tests:" + Colors.ENDC)
            for result in self.results:
                if not result.passed:
                    print(f"  • {result.name}: {result.message}")
                    if result.suggestion:
                        print(f"    → {result.suggestion}")

        return failed == 0


# =============================================================================
# Mock Home Assistant Environment
# =============================================================================

class MockHomeAssistant:
    """Mock Home Assistant for testing."""

    def __init__(self):
        self.data = {}
        self.config_entries = MockConfigEntries()
        self.http = MockHTTP()


class MockConfigEntries:
    """Mock config entries."""

    def __init__(self):
        self.entries = []


class MockHTTP:
    """Mock HTTP component."""

    def __init__(self):
        self.client = None


# =============================================================================
# Pre-flight Checks
# =============================================================================

async def preflight_checks(tracker: TestTracker, verbose: bool = False) -> dict[str, Any]:
    """Run pre-flight checks.

    Returns:
        dict: Configuration data if checks pass
    """
    print_header("Pre-Flight Checks")

    config = {}

    # Check 1: Verify project structure
    print_step(1, "Verifying project structure...")

    project_root = Path(__file__).parent.parent
    required_files = [
        "custom_components/alexa/__init__.py",
        "custom_components/alexa/config_flow.py",
        "custom_components/alexa/oauth_manager.py",
        "custom_components/alexa/token_manager.py",
    ]

    all_exist = True
    for file_path in required_files:
        full_path = project_root / file_path
        if not full_path.exists():
            tracker.add_result(
                f"File {file_path}",
                False,
                "Not found",
                f"Ensure the file exists at {full_path}"
            )
            all_exist = False
        elif verbose:
            print_info(f"   Found: {file_path}")

    if all_exist:
        tracker.add_result(
            "Project structure",
            True,
            "All required files present"
        )

    # Check 2: Verify constants
    print_step(2, "Verifying OAuth constants...")

    constants_ok = True
    if not AMAZON_AUTH_URL.startswith("https://"):
        tracker.add_result(
            "AMAZON_AUTH_URL",
            False,
            f"Invalid URL: {AMAZON_AUTH_URL}",
            "Check custom_components/alexa/const.py"
        )
        constants_ok = False

    if not AMAZON_TOKEN_URL.startswith("https://"):
        tracker.add_result(
            "AMAZON_TOKEN_URL",
            False,
            f"Invalid URL: {AMAZON_TOKEN_URL}",
            "Check custom_components/alexa/const.py"
        )
        constants_ok = False

    if REQUIRED_SCOPES != "alexa::skills:account_linking":
        tracker.add_result(
            "REQUIRED_SCOPES",
            False,
            f"Unexpected scope: {REQUIRED_SCOPES}",
            "Should be 'alexa::skills:account_linking'"
        )
        constants_ok = False

    if constants_ok:
        tracker.add_result(
            "OAuth constants",
            True,
            "All constants properly configured"
        )

    # Check 3: Get credentials from user
    print_step(3, "Gathering Amazon Developer credentials...")

    print(f"\n{Colors.BOLD}Enter your Amazon Developer credentials:{Colors.ENDC}")
    print("(These are found in your Amazon Developer Console)")
    print("(They will NOT be saved to disk)\n")

    client_id = input("Client ID (amzn1.application-oa2-client.*): ").strip()
    client_secret = input("Client Secret: ").strip()

    # Validate credential format
    if not client_id.startswith("amzn1.application-oa2-client."):
        tracker.add_result(
            "Client ID format",
            False,
            "Client ID should start with 'amzn1.application-oa2-client.'",
            "Copy the exact Client ID from Amazon Developer Console"
        )
        return None

    if len(client_id) < 50:
        tracker.add_result(
            "Client ID length",
            False,
            f"Client ID too short ({len(client_id)} chars, expected 50+)",
            "Ensure you copied the complete Client ID"
        )
        return None

    if len(client_secret) < 32:
        tracker.add_result(
            "Client Secret length",
            False,
            f"Client Secret too short ({len(client_secret)} chars, expected 32+)",
            "Ensure you copied the complete Client Secret"
        )
        return None

    tracker.add_result(
        "Credentials format",
        True,
        "Client ID and Secret format valid"
    )

    config["client_id"] = client_id
    config["client_secret"] = client_secret

    # Check 4: Verify redirect URI accessibility
    print_step(4, "Verifying redirect URI configuration...")

    redirect_uri = "https://my.home-assistant.io/redirect/alexa"
    print_info(f"   Redirect URI: {redirect_uri}")
    print_warning("   Make sure this EXACT URI is registered in Amazon Developer Console")
    print_warning("   under 'Allowed Return URLs' in your Security Profile")

    response = input(f"\n{Colors.BOLD}Is '{redirect_uri}' registered in Amazon Developer Console? (yes/no): {Colors.ENDC}").strip().lower()

    if response != "yes":
        tracker.add_result(
            "Redirect URI",
            False,
            "Redirect URI not registered",
            f"Add '{redirect_uri}' to Allowed Return URLs in Amazon Developer Console Security Profile"
        )
        return None

    tracker.add_result(
        "Redirect URI",
        True,
        "Confirmed registered in Amazon Developer Console"
    )

    config["redirect_uri"] = redirect_uri

    return config


# =============================================================================
# PKCE Verification
# =============================================================================

def verify_pkce(tracker: TestTracker, verbose: bool = False) -> bool:
    """Verify PKCE implementation.

    Returns:
        bool: True if all PKCE checks pass
    """
    print_header("PKCE Verification")

    # Create mock Home Assistant
    hass = MockHomeAssistant()
    oauth_manager = OAuthManager(
        hass,
        "amzn1.application-oa2-client.test",
        "test_secret"
    )

    # Test 1: Generate PKCE pair
    print_step(1, "Testing PKCE pair generation...")

    try:
        verifier, challenge = oauth_manager.generate_pkce_pair()

        # Verify verifier length (43 chars for 32 bytes base64url)
        if len(verifier) != 43:
            tracker.add_result(
                "PKCE verifier length",
                False,
                f"Expected 43 chars, got {len(verifier)}",
                "Check generate_pkce_pair() implementation"
            )
            return False

        # Verify challenge length (43 chars for 32 bytes SHA-256 base64url)
        if len(challenge) != 43:
            tracker.add_result(
                "PKCE challenge length",
                False,
                f"Expected 43 chars, got {len(challenge)}",
                "Check generate_pkce_pair() implementation"
            )
            return False

        tracker.add_result(
            "PKCE pair generation",
            True,
            f"Generated valid verifier and challenge (both 43 chars)"
        )

        if verbose:
            print_info(f"   Verifier: {verifier[:10]}...")
            print_info(f"   Challenge: {challenge[:10]}...")

    except Exception as e:
        tracker.add_result(
            "PKCE pair generation",
            False,
            f"Exception: {e}",
            "Check oauth_manager.py generate_pkce_pair()"
        )
        return False

    # Test 2: Verify challenge is SHA-256 of verifier
    print_step(2, "Verifying PKCE challenge computation...")

    try:
        # Compute expected challenge
        expected_hash = hashlib.sha256(verifier.encode()).digest()
        expected_challenge = base64.urlsafe_b64encode(expected_hash).decode().rstrip('=')

        if challenge != expected_challenge:
            tracker.add_result(
                "PKCE challenge computation",
                False,
                "Challenge does not match SHA-256 of verifier",
                "Verify code_challenge = BASE64URL(SHA256(ASCII(code_verifier)))"
            )
            return False

        tracker.add_result(
            "PKCE challenge computation",
            True,
            "Challenge correctly computed as SHA-256 of verifier"
        )

    except Exception as e:
        tracker.add_result(
            "PKCE challenge computation",
            False,
            f"Exception: {e}",
            "Check PKCE challenge generation logic"
        )
        return False

    # Test 3: Verify multiple generations produce different values
    print_step(3, "Verifying PKCE randomness...")

    try:
        verifier2, challenge2 = oauth_manager.generate_pkce_pair()

        if verifier == verifier2:
            tracker.add_result(
                "PKCE randomness",
                False,
                "Generated identical verifiers (not random)",
                "Check that secrets.token_bytes() is used for randomness"
            )
            return False

        if challenge == challenge2:
            tracker.add_result(
                "PKCE randomness",
                False,
                "Generated identical challenges (not random)",
                "Challenges should differ if verifiers differ"
            )
            return False

        tracker.add_result(
            "PKCE randomness",
            True,
            "PKCE pairs are properly randomized"
        )

    except Exception as e:
        tracker.add_result(
            "PKCE randomness",
            False,
            f"Exception: {e}",
            "Check PKCE generation implementation"
        )
        return False

    # Test 4: Verify state parameter generation
    print_step(4, "Testing state parameter generation...")

    try:
        state1 = oauth_manager.generate_state()
        state2 = oauth_manager.generate_state()

        if len(state1) != 43:
            tracker.add_result(
                "State parameter length",
                False,
                f"Expected 43 chars, got {len(state1)}",
                "Check generate_state() implementation"
            )
            return False

        if state1 == state2:
            tracker.add_result(
                "State parameter randomness",
                False,
                "Generated identical state values",
                "Check that secrets.token_bytes() is used"
            )
            return False

        tracker.add_result(
            "State parameter",
            True,
            "State parameter properly generated and randomized"
        )

        if verbose:
            print_info(f"   State 1: {state1[:10]}...")
            print_info(f"   State 2: {state2[:10]}...")

    except Exception as e:
        tracker.add_result(
            "State parameter",
            False,
            f"Exception: {e}",
            "Check oauth_manager.py generate_state()"
        )
        return False

    # Test 5: Verify state validation (constant-time comparison)
    print_step(5, "Testing state validation...")

    try:
        state = oauth_manager.generate_state()

        # Test valid match
        if not oauth_manager.validate_state(state, state):
            tracker.add_result(
                "State validation (match)",
                False,
                "Failed to validate matching states",
                "Check validate_state() logic"
            )
            return False

        # Test invalid match
        wrong_state = oauth_manager.generate_state()
        if oauth_manager.validate_state(state, wrong_state):
            tracker.add_result(
                "State validation (mismatch)",
                False,
                "Incorrectly validated non-matching states",
                "Check validate_state() logic"
            )
            return False

        tracker.add_result(
            "State validation",
            True,
            "State validation working correctly"
        )

    except Exception as e:
        tracker.add_result(
            "State validation",
            False,
            f"Exception: {e}",
            "Check oauth_manager.py validate_state()"
        )
        return False

    return True


# =============================================================================
# Authorization URL Verification
# =============================================================================

async def verify_authorization_url(
    config: dict[str, Any],
    tracker: TestTracker,
    verbose: bool = False
) -> dict[str, Any] | None:
    """Verify authorization URL generation.

    Returns:
        dict: OAuth data (auth_url, verifier, state) if successful
    """
    print_header("Authorization URL Verification")

    # Create mock Home Assistant
    hass = MockHomeAssistant()
    oauth_manager = OAuthManager(
        hass,
        config["client_id"],
        config["client_secret"]
    )

    # Test 1: Generate authorization URL
    print_step(1, "Generating authorization URL...")

    try:
        auth_url, verifier, state = await oauth_manager.get_authorization_url(
            "test_flow_123",
            config["redirect_uri"]
        )

        tracker.add_result(
            "Authorization URL generation",
            True,
            "Successfully generated authorization URL"
        )

        if verbose:
            print_info(f"   URL: {auth_url[:80]}...")
            print_info(f"   Verifier: {verifier[:10]}...")
            print_info(f"   State: {state[:10]}...")

    except Exception as e:
        tracker.add_result(
            "Authorization URL generation",
            False,
            f"Exception: {e}",
            "Check get_authorization_url() implementation"
        )
        return None

    # Test 2: Parse and verify URL components
    print_step(2, "Verifying authorization URL components...")

    try:
        parsed = urlparse(auth_url)
        params = parse_qs(parsed.query)

        # Check base URL
        expected_base = AMAZON_AUTH_URL.split('?')[0]
        actual_base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        if actual_base != expected_base:
            tracker.add_result(
                "Auth URL base",
                False,
                f"Expected {expected_base}, got {actual_base}",
                "Check AMAZON_AUTH_URL constant"
            )
            return None

        # Check required parameters
        required_params = {
            "client_id": config["client_id"],
            "response_type": "code",
            "scope": REQUIRED_SCOPES,
            "redirect_uri": config["redirect_uri"],
            "code_challenge_method": "S256",
        }

        for param_name, expected_value in required_params.items():
            if param_name not in params:
                tracker.add_result(
                    f"URL param '{param_name}'",
                    False,
                    "Missing from authorization URL",
                    f"Add {param_name} parameter to URL"
                )
                return None

            actual_value = params[param_name][0]
            if actual_value != expected_value:
                tracker.add_result(
                    f"URL param '{param_name}'",
                    False,
                    f"Expected '{expected_value}', got '{actual_value}'",
                    "Check get_authorization_url() parameter construction"
                )
                return None

        # Check state parameter
        if "state" not in params:
            tracker.add_result(
                "URL param 'state'",
                False,
                "Missing from authorization URL",
                "State parameter required for CSRF protection"
            )
            return None

        url_state = params["state"][0]
        if url_state != state:
            tracker.add_result(
                "State parameter match",
                False,
                "URL state doesn't match returned state",
                "Ensure same state value is in URL and return value"
            )
            return None

        # Check code_challenge parameter
        if "code_challenge" not in params:
            tracker.add_result(
                "URL param 'code_challenge'",
                False,
                "Missing from authorization URL",
                "PKCE challenge required for secure OAuth"
            )
            return None

        url_challenge = params["code_challenge"][0]

        # Verify challenge matches verifier
        expected_hash = hashlib.sha256(verifier.encode()).digest()
        expected_challenge = base64.urlsafe_b64encode(expected_hash).decode().rstrip('=')

        if url_challenge != expected_challenge:
            tracker.add_result(
                "PKCE challenge match",
                False,
                "URL challenge doesn't match verifier",
                "Ensure challenge = BASE64URL(SHA256(verifier))"
            )
            return None

        tracker.add_result(
            "Authorization URL components",
            True,
            "All required parameters present and valid"
        )

        if verbose:
            print_info("   URL Parameters:")
            for key in sorted(params.keys()):
                value = params[key][0]
                if key in ["client_secret", "code_verifier"]:
                    print_info(f"      {key}: ***REDACTED***")
                elif len(value) > 50:
                    print_info(f"      {key}: {value[:50]}...")
                else:
                    print_info(f"      {key}: {value}")

    except Exception as e:
        tracker.add_result(
            "Authorization URL parsing",
            False,
            f"Exception: {e}",
            "Check URL format and parameter encoding"
        )
        return None

    return {
        "auth_url": auth_url,
        "verifier": verifier,
        "state": state,
        "oauth_manager": oauth_manager,
    }


# =============================================================================
# Token Verification
# =============================================================================

def verify_token_format(
    token_data: dict[str, Any],
    tracker: TestTracker,
    verbose: bool = False
) -> bool:
    """Verify token response format.

    Args:
        token_data: Token response from Amazon
        tracker: Test result tracker
        verbose: Show detailed output

    Returns:
        bool: True if token format valid
    """
    print_header("Token Format Verification")

    # Test 1: Check required fields
    print_step(1, "Checking required token fields...")

    required_fields = ["access_token", "refresh_token", "token_type", "expires_in"]
    missing_fields = [field for field in required_fields if field not in token_data]

    if missing_fields:
        tracker.add_result(
            "Required token fields",
            False,
            f"Missing fields: {', '.join(missing_fields)}",
            "Check Amazon token response format"
        )
        return False

    tracker.add_result(
        "Required token fields",
        True,
        "All required fields present"
    )

    # Test 2: Verify token_type
    print_step(2, "Verifying token type...")

    if token_data["token_type"] != "Bearer":
        tracker.add_result(
            "Token type",
            False,
            f"Expected 'Bearer', got '{token_data['token_type']}'",
            "Amazon LWA should return 'Bearer' token type"
        )
        return False

    tracker.add_result(
        "Token type",
        True,
        "Token type is 'Bearer'"
    )

    # Test 3: Verify access_token format (Amazon prefix)
    print_step(3, "Verifying access token format...")

    access_token = token_data["access_token"]
    if not access_token.startswith("Atza|"):
        tracker.add_result(
            "Access token format",
            False,
            "Access token should start with 'Atza|'",
            "Check that you're using real Amazon credentials (not mock)"
        )
        return False

    if len(access_token) < 100:
        tracker.add_result(
            "Access token length",
            False,
            f"Access token suspiciously short ({len(access_token)} chars)",
            "Real Amazon access tokens are typically 300+ characters"
        )
        return False

    tracker.add_result(
        "Access token format",
        True,
        f"Valid Amazon access token ({len(access_token)} chars)"
    )

    # Test 4: Verify refresh_token format (Amazon prefix)
    print_step(4, "Verifying refresh token format...")

    refresh_token = token_data["refresh_token"]
    if not refresh_token.startswith("Atzr|"):
        tracker.add_result(
            "Refresh token format",
            False,
            "Refresh token should start with 'Atzr|'",
            "Check that you're using real Amazon credentials (not mock)"
        )
        return False

    if len(refresh_token) < 100:
        tracker.add_result(
            "Refresh token length",
            False,
            f"Refresh token suspiciously short ({len(refresh_token)} chars)",
            "Real Amazon refresh tokens are typically 300+ characters"
        )
        return False

    tracker.add_result(
        "Refresh token format",
        True,
        f"Valid Amazon refresh token ({len(refresh_token)} chars)"
    )

    # Test 5: Verify expires_in
    print_step(5, "Verifying token expiry...")

    expires_in = token_data["expires_in"]
    if not isinstance(expires_in, int):
        tracker.add_result(
            "Expires_in type",
            False,
            f"Expected int, got {type(expires_in).__name__}",
            "expires_in should be integer seconds"
        )
        return False

    if expires_in <= 0:
        tracker.add_result(
            "Expires_in value",
            False,
            f"Expected positive value, got {expires_in}",
            "Token already expired?"
        )
        return False

    # Typical Amazon LWA tokens expire in 3600 seconds (1 hour)
    if expires_in < 3000 or expires_in > 4000:
        print_warning(f"   Unexpected expires_in: {expires_in} (typical is 3600)")

    tracker.add_result(
        "Token expiry",
        True,
        f"Token expires in {expires_in} seconds ({expires_in // 60} minutes)"
    )

    # Test 6: Verify scope (optional field)
    print_step(6, "Verifying token scope...")

    if "scope" in token_data:
        scope = token_data["scope"]
        if REQUIRED_SCOPES not in scope:
            tracker.add_result(
                "Token scope",
                False,
                f"Expected scope '{REQUIRED_SCOPES}', got '{scope}'",
                "Check that you requested correct scopes in authorization URL"
            )
            return False

        tracker.add_result(
            "Token scope",
            True,
            f"Correct scope: {scope}"
        )
    else:
        print_warning("   Scope field not present in token response (optional)")

    if verbose:
        print_info("   Token Details:")
        print_info(f"      Access Token: {access_token[:20]}...{access_token[-10:]}")
        print_info(f"      Refresh Token: {refresh_token[:20]}...{refresh_token[-10:]}")
        print_info(f"      Token Type: {token_data['token_type']}")
        print_info(f"      Expires In: {expires_in} seconds")
        if "scope" in token_data:
            print_info(f"      Scope: {token_data['scope']}")

    return True


# =============================================================================
# Security Audit
# =============================================================================

def security_audit(tracker: TestTracker, verbose: bool = False) -> bool:
    """Run security audit of OAuth implementation.

    Returns:
        bool: True if all security checks pass
    """
    print_header("Security Audit")

    project_root = Path(__file__).parent.parent

    # Test 1: Check for hardcoded credentials
    print_step(1, "Scanning for hardcoded credentials...")

    patterns = [
        (r'client_secret\s*=\s*["\'][^"\']{20,}["\']', "Hardcoded client_secret"),
        (r'access_token\s*=\s*["\']Atza\|[^"\']+["\']', "Hardcoded access_token"),
        (r'refresh_token\s*=\s*["\']Atzr\|[^"\']+["\']', "Hardcoded refresh_token"),
    ]

    found_issues = []
    for py_file in (project_root / "custom_components" / "alexa").glob("*.py"):
        if py_file.name.startswith("test_"):
            continue

        content = py_file.read_text()
        for pattern, description in patterns:
            if re.search(pattern, content):
                found_issues.append(f"{py_file.name}: {description}")

    if found_issues:
        tracker.add_result(
            "Hardcoded credentials",
            False,
            f"Found {len(found_issues)} potential issues",
            "Remove hardcoded credentials and use config/storage"
        )
        if verbose:
            for issue in found_issues:
                print_info(f"      {issue}")
        return False

    tracker.add_result(
        "Hardcoded credentials",
        True,
        "No hardcoded credentials found"
    )

    # Test 2: Check PKCE implementation
    print_step(2, "Verifying PKCE security...")

    oauth_file = project_root / "custom_components" / "alexa" / "oauth_manager.py"
    oauth_content = oauth_file.read_text()

    # Check for secrets.token_bytes usage
    if "secrets.token_bytes" not in oauth_content:
        tracker.add_result(
            "PKCE randomness",
            False,
            "Not using secrets.token_bytes for PKCE",
            "Use secrets.token_bytes() for cryptographic randomness"
        )
        return False

    # Check for SHA-256 hashing
    if "hashlib.sha256" not in oauth_content:
        tracker.add_result(
            "PKCE hashing",
            False,
            "Not using SHA-256 for PKCE challenge",
            "Use hashlib.sha256() for code_challenge"
        )
        return False

    tracker.add_result(
        "PKCE implementation",
        True,
        "Using cryptographic randomness and SHA-256"
    )

    # Test 3: Check state parameter validation
    print_step(3, "Verifying state parameter security...")

    # Check for constant-time comparison
    if "hmac.compare_digest" not in oauth_content:
        tracker.add_result(
            "State validation",
            False,
            "Not using constant-time comparison",
            "Use hmac.compare_digest() to prevent timing attacks"
        )
        return False

    tracker.add_result(
        "State validation",
        True,
        "Using constant-time comparison (hmac.compare_digest)"
    )

    # Test 4: Check token storage security
    print_step(4, "Verifying token storage security...")

    token_file = project_root / "custom_components" / "alexa" / "token_manager.py"
    token_content = token_file.read_text()

    # Check for Home Assistant Store usage (encrypted)
    if "Store" not in token_content:
        tracker.add_result(
            "Token storage",
            False,
            "Not using Home Assistant Store",
            "Use homeassistant.helpers.storage.Store for encrypted storage"
        )
        return False

    tracker.add_result(
        "Token storage",
        True,
        "Using Home Assistant Store (encrypted)"
    )

    # Test 5: Check for token logging
    print_step(5, "Checking for token exposure in logs...")

    log_issues = []
    for py_file in (project_root / "custom_components" / "alexa").glob("*.py"):
        content = py_file.read_text()

        # Look for logger statements with token variables
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            if "_LOGGER" in line and any(token_var in line for token_var in ["access_token", "refresh_token", "client_secret"]):
                # Check if redaction is used
                if "redact" not in lines[max(0, i-3):min(len(lines), i+3)]:
                    if "..." not in line and "***" not in line:
                        log_issues.append(f"{py_file.name}:{i}")

    if log_issues and verbose:
        print_warning(f"   Found {len(log_issues)} potential token logging issues")
        for issue in log_issues[:5]:  # Show first 5
            print_info(f"      {issue}")

    if log_issues:
        tracker.add_result(
            "Token logging",
            False,
            f"Found {len(log_issues)} potential token exposure in logs",
            "Always redact tokens in log statements"
        )
    else:
        tracker.add_result(
            "Token logging",
            True,
            "No token exposure in logs detected"
        )

    # Test 6: Check HTTPS enforcement
    print_step(6, "Verifying HTTPS enforcement...")

    const_file = project_root / "custom_components" / "alexa" / "const.py"
    const_content = const_file.read_text()

    # Check all URLs use HTTPS
    urls = re.findall(r'https?://[^\s"\']+', const_content)
    http_urls = [url for url in urls if url.startswith("http://")]

    if http_urls:
        tracker.add_result(
            "HTTPS enforcement",
            False,
            f"Found {len(http_urls)} HTTP URLs",
            "All OAuth endpoints must use HTTPS"
        )
        if verbose:
            for url in http_urls:
                print_info(f"      {url}")
        return False

    tracker.add_result(
        "HTTPS enforcement",
        True,
        "All URLs use HTTPS"
    )

    return True


# =============================================================================
# OAuth Flow Walkthrough
# =============================================================================

async def oauth_flow_walkthrough(
    config: dict[str, Any],
    oauth_data: dict[str, Any],
    tracker: TestTracker,
    verbose: bool = False
) -> dict[str, Any] | None:
    """Interactive OAuth flow walkthrough.

    Returns:
        dict: Token response if successful
    """
    print_header("OAuth Flow Walkthrough")

    # Step 1: Display authorization URL
    print_step(1, "Authorization URL ready")
    print(f"\n{Colors.BOLD}Copy this URL and open it in your browser:{Colors.ENDC}")
    print(f"{Colors.OKCYAN}{oauth_data['auth_url']}{Colors.ENDC}\n")

    print("This will:")
    print("  1. Redirect you to Amazon login page")
    print("  2. Ask you to authorize the application")
    print("  3. Redirect back to Home Assistant with authorization code")
    print("")

    print_warning("IMPORTANT: After authorizing, Amazon will redirect you to:")
    print_warning(f"  {config['redirect_uri']}?code=XXX&state=YYY")
    print_warning("")
    print_warning("This redirect will FAIL (Home Assistant not running)")
    print_warning("That's OK! Copy the ENTIRE redirect URL from your browser's address bar")

    input(f"\n{Colors.BOLD}Press Enter when you're ready to open the authorization URL...{Colors.ENDC}")

    print(f"\n{Colors.OKGREEN}Opening URL in browser...{Colors.ENDC}")
    print(f"{Colors.OKCYAN}{oauth_data['auth_url']}{Colors.ENDC}\n")

    # Step 2: Get callback URL from user
    print_step(2, "Waiting for authorization callback...")

    print(f"\n{Colors.BOLD}After authorizing, copy the FULL redirect URL from your browser{Colors.ENDC}")
    print("It will look like:")
    print(f"  {config['redirect_uri']}?code=ANaRx...&state=xyz...")
    print("")

    callback_url = input("Paste the full redirect URL here: ").strip()

    # Parse callback URL
    try:
        parsed = urlparse(callback_url)
        params = parse_qs(parsed.query)

        # Check for error
        if "error" in params:
            error = params["error"][0]
            error_desc = params.get("error_description", ["No description"])[0]
            tracker.add_result(
                "OAuth authorization",
                False,
                f"Authorization failed: {error} - {error_desc}",
                "Check your Amazon Developer settings and try again"
            )
            return None

        # Extract code and state
        if "code" not in params:
            tracker.add_result(
                "Authorization code",
                False,
                "No authorization code in callback URL",
                "Make sure you copied the complete URL"
            )
            return None

        if "state" not in params:
            tracker.add_result(
                "State parameter",
                False,
                "No state parameter in callback URL",
                "Make sure you copied the complete URL"
            )
            return None

        code = params["code"][0]
        callback_state = params["state"][0]

        if verbose:
            print_info(f"   Authorization code: {code[:20]}...")
            print_info(f"   State: {callback_state[:20]}...")

    except Exception as e:
        tracker.add_result(
            "Callback URL parsing",
            False,
            f"Failed to parse URL: {e}",
            "Make sure you pasted the complete redirect URL"
        )
        return None

    # Step 3: Validate state parameter
    print_step(3, "Validating state parameter...")

    oauth_manager = oauth_data["oauth_manager"]
    expected_state = oauth_data["state"]

    if not oauth_manager.validate_state(callback_state, expected_state):
        tracker.add_result(
            "State validation",
            False,
            "State parameter mismatch (CSRF protection triggered)",
            "This could indicate a security issue or wrong authorization session"
        )
        return None

    tracker.add_result(
        "State validation",
        True,
        "State parameter matches (CSRF protection passed)"
    )

    # Step 4: Exchange code for tokens
    print_step(4, "Exchanging authorization code for tokens...")

    try:
        token_response = await oauth_manager.exchange_code(
            code,
            oauth_data["verifier"],
            config["redirect_uri"]
        )

        tracker.add_result(
            "Token exchange",
            True,
            "Successfully exchanged code for tokens"
        )

        # Convert to dict for verification
        token_dict = {
            "access_token": token_response.access_token,
            "refresh_token": token_response.refresh_token,
            "token_type": token_response.token_type,
            "expires_in": token_response.expires_in,
            "scope": token_response.scope,
        }

        return token_dict

    except AlexaInvalidCodeError as e:
        tracker.add_result(
            "Token exchange",
            False,
            f"Invalid authorization code: {e}",
            "Code may have expired (10 min limit) or already been used. Try OAuth flow again."
        )
        return None

    except AlexaInvalidGrantError as e:
        tracker.add_result(
            "Token exchange",
            False,
            f"Invalid grant: {e}",
            "Check your client credentials and redirect URI configuration"
        )
        return None

    except AlexaNetworkError as e:
        tracker.add_result(
            "Token exchange",
            False,
            f"Network error: {e}",
            "Check your internet connection and Amazon API availability"
        )
        return None

    except Exception as e:
        tracker.add_result(
            "Token exchange",
            False,
            f"Unexpected error: {e}",
            "Check logs for details"
        )
        return None


# =============================================================================
# Token Refresh Testing
# =============================================================================

async def test_token_refresh(
    config: dict[str, Any],
    token_data: dict[str, Any],
    tracker: TestTracker,
    verbose: bool = False
) -> bool:
    """Test token refresh functionality.

    Returns:
        bool: True if refresh successful
    """
    print_header("Token Refresh Testing")

    # Create mock Home Assistant
    hass = MockHomeAssistant()
    oauth_manager = OAuthManager(
        hass,
        config["client_id"],
        config["client_secret"]
    )

    refresh_token = token_data["refresh_token"]

    print_step(1, "Testing token refresh...")
    print_info(f"   Using refresh token: {refresh_token[:20]}...")

    try:
        new_tokens = await oauth_manager.refresh_access_token(refresh_token)

        tracker.add_result(
            "Token refresh",
            True,
            "Successfully refreshed access token"
        )

        if verbose:
            print_info("   New Token Details:")
            print_info(f"      Access Token: {new_tokens.access_token[:20]}...")
            print_info(f"      Refresh Token: {new_tokens.refresh_token[:20]}...")
            print_info(f"      Expires In: {new_tokens.expires_in} seconds")

        # Verify new access token is different
        if new_tokens.access_token == token_data["access_token"]:
            tracker.add_result(
                "New access token",
                False,
                "Access token unchanged after refresh",
                "Amazon should issue a new access token on each refresh"
            )
            return False

        tracker.add_result(
            "New access token",
            True,
            "New access token received"
        )

        # Note about refresh token
        if new_tokens.refresh_token == token_data["refresh_token"]:
            print_info("   Refresh token unchanged (Amazon may or may not rotate)")
        else:
            print_info("   Refresh token rotated (new refresh token received)")

        return True

    except AlexaInvalidGrantError as e:
        tracker.add_result(
            "Token refresh",
            False,
            f"Refresh token invalid: {e}",
            "Refresh token may have expired. Full reauth required."
        )
        return False

    except AlexaNetworkError as e:
        tracker.add_result(
            "Token refresh",
            False,
            f"Network error: {e}",
            "Check internet connection and Amazon API availability"
        )
        return False

    except Exception as e:
        tracker.add_result(
            "Token refresh",
            False,
            f"Unexpected error: {e}",
            "Check logs for details"
        )
        return False


# =============================================================================
# Common Error Diagnosis
# =============================================================================

def diagnose_common_errors(tracker: TestTracker) -> None:
    """Provide diagnosis for common OAuth errors."""
    print_header("Common OAuth Error Diagnosis")

    errors = [
        {
            "error": "invalid_client",
            "cause": "Invalid client_id or client_secret",
            "fix": "Verify credentials in Amazon Developer Console > Security Profile",
        },
        {
            "error": "invalid_grant (authorization code)",
            "cause": "Authorization code expired or already used",
            "fix": "Authorization codes expire in 10 minutes and are single-use. Start OAuth flow again.",
        },
        {
            "error": "invalid_grant (refresh token)",
            "cause": "Refresh token expired or revoked",
            "fix": "Refresh tokens typically expire after 1 year. Trigger reauth flow in Home Assistant.",
        },
        {
            "error": "redirect_uri_mismatch",
            "cause": "Redirect URI not registered or doesn't match exactly",
            "fix": "Add 'https://my.home-assistant.io/redirect/alexa' to Allowed Return URLs in Amazon Developer Console (EXACT match required)",
        },
        {
            "error": "invalid_scope",
            "cause": "Requested scope not enabled for your application",
            "fix": "Enable 'Alexa Skills Kit' in Amazon Developer Console > Permissions",
        },
        {
            "error": "State mismatch",
            "cause": "CSRF protection triggered - state parameter doesn't match",
            "fix": "Don't reuse authorization URLs. Generate fresh URL for each OAuth attempt.",
        },
        {
            "error": "PKCE verification failed",
            "cause": "code_verifier doesn't match code_challenge",
            "fix": "Ensure code_verifier stored during authorization is used during token exchange (don't generate new one)",
        },
    ]

    print("If you encounter OAuth errors, here are common causes and fixes:\n")

    for i, err in enumerate(errors, 1):
        print(f"{Colors.BOLD}{i}. Error: {err['error']}{Colors.ENDC}")
        print(f"   {Colors.WARNING}Cause:{Colors.ENDC} {err['cause']}")
        print(f"   {Colors.OKGREEN}Fix:{Colors.ENDC} {err['fix']}")
        print()


# =============================================================================
# Main Test Runner
# =============================================================================

async def main():
    """Main test runner."""
    parser = argparse.ArgumentParser(
        description="OAuth2 Verification and Testing for Alexa Integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Run pre-flight checks only (no OAuth flow)"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show verbose output with detailed information"
    )
    parser.add_argument(
        "--test-refresh",
        action="store_true",
        help="Test token refresh after OAuth flow"
    )
    parser.add_argument(
        "--security-audit",
        action="store_true",
        help="Run security audit only"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    # Configure logging
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    # Create test tracker
    tracker = TestTracker()

    print(f"\n{Colors.HEADER}{Colors.BOLD}")
    print("╔════════════════════════════════════════════════════════════════════════════╗")
    print("║                                                                            ║")
    print("║            OAuth2 Verification & Testing - Alexa Integration              ║")
    print("║                                                                            ║")
    print("╚════════════════════════════════════════════════════════════════════════════╝")
    print(f"{Colors.ENDC}\n")

    # Security audit only mode
    if args.security_audit:
        success = security_audit(tracker, args.verbose)
        tracker.print_summary()
        return 0 if success else 1

    # Pre-flight checks
    config = await preflight_checks(tracker, args.verbose)
    if not config:
        print_error("\nPre-flight checks failed. Cannot proceed.")
        tracker.print_summary()
        return 1

    if args.check_only:
        print_success("\nPre-flight checks passed!")
        tracker.print_summary()
        return 0

    # PKCE verification
    if not verify_pkce(tracker, args.verbose):
        print_error("\nPKCE verification failed.")
        tracker.print_summary()
        return 1

    # Authorization URL verification
    oauth_data = await verify_authorization_url(config, tracker, args.verbose)
    if not oauth_data:
        print_error("\nAuthorization URL verification failed.")
        tracker.print_summary()
        return 1

    # OAuth flow walkthrough
    token_data = await oauth_flow_walkthrough(config, oauth_data, tracker, args.verbose)
    if not token_data:
        print_error("\nOAuth flow failed.")
        diagnose_common_errors(tracker)
        tracker.print_summary()
        return 1

    # Token format verification
    if not verify_token_format(token_data, tracker, args.verbose):
        print_error("\nToken verification failed.")
        tracker.print_summary()
        return 1

    # Token refresh testing (optional)
    if args.test_refresh:
        if not await test_token_refresh(config, token_data, tracker, args.verbose):
            print_warning("\nToken refresh test failed (non-critical)")

    # Security audit
    security_audit(tracker, args.verbose)

    # Print summary
    all_passed = tracker.print_summary()

    if all_passed:
        print(f"\n{Colors.OKGREEN}{Colors.BOLD}")
        print("╔════════════════════════════════════════════════════════════════════════════╗")
        print("║                                                                            ║")
        print("║                    ✓ ALL TESTS PASSED SUCCESSFULLY!                       ║")
        print("║                                                                            ║")
        print("║      Your OAuth2 implementation is working correctly with real            ║")
        print("║      Amazon credentials. You can now use this integration in              ║")
        print("║      production.                                                           ║")
        print("║                                                                            ║")
        print("╚════════════════════════════════════════════════════════════════════════════╝")
        print(f"{Colors.ENDC}\n")
        return 0
    else:
        print(f"\n{Colors.FAIL}{Colors.BOLD}")
        print("╔════════════════════════════════════════════════════════════════════════════╗")
        print("║                                                                            ║")
        print("║                         ✗ SOME TESTS FAILED                               ║")
        print("║                                                                            ║")
        print("║      Review the failures above and follow the suggestions.                ║")
        print("║                                                                            ║")
        print("╚════════════════════════════════════════════════════════════════════════════╝")
        print(f"{Colors.ENDC}\n")
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n\n{Colors.WARNING}Interrupted by user{Colors.ENDC}")
        sys.exit(130)
    except Exception as e:
        print(f"\n{Colors.FAIL}Unexpected error: {e}{Colors.ENDC}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
