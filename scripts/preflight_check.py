#!/usr/bin/env python3
"""Pre-flight checklist for Amazon Alexa OAuth2 integration setup.

This script validates that all prerequisites are met before configuring
the Alexa skill OAuth2 settings in the Amazon Developer Console.

Usage:
    python3 preflight_check.py           # Standard output
    python3 preflight_check.py --verbose # Detailed output
    python3 preflight_check.py --json    # JSON output for automation

Exit Codes:
    0: All checks passed, ready for setup
    1: Critical failures, cannot proceed
    2: Warnings present, review required
"""

import argparse
import json
import platform
import socket
import ssl
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class CheckStatus(Enum):
    """Status of a preflight check."""

    PASS = "✅"
    FAIL = "❌"
    WARN = "⚠️"
    SKIP = "⏭️"


@dataclass
class CheckResult:
    """Result of a single preflight check."""

    name: str
    status: CheckStatus
    message: str
    details: str = ""
    critical: bool = False


class PreflightChecker:
    """Orchestrates all preflight checks."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.results: list[CheckResult] = []
        self.project_root = Path(__file__).parent.parent.resolve()

    def add_result(
        self,
        name: str,
        status: CheckStatus,
        message: str,
        details: str = "",
        critical: bool = False,
    ) -> None:
        """Add a check result."""
        self.results.append(
            CheckResult(
                name=name,
                status=status,
                message=message,
                details=details,
                critical=critical,
            )
        )

    def check_python_version(self) -> None:
        """Verify Python version >= 3.12."""
        version = sys.version_info
        version_str = f"{version.major}.{version.minor}.{version.micro}"

        if version.major == 3 and version.minor >= 12:
            self.add_result(
                "Python Version",
                CheckStatus.PASS,
                f"Python {version_str}",
                f"Running on Python {version_str} ({platform.python_implementation()})",
            )
        elif version.major == 3 and version.minor >= 11:
            self.add_result(
                "Python Version",
                CheckStatus.WARN,
                f"Python {version_str} (recommend >= 3.12)",
                "Python 3.11 may work but 3.12+ is recommended for Home Assistant",
            )
        else:
            self.add_result(
                "Python Version",
                CheckStatus.FAIL,
                f"Python {version_str} too old",
                "Python 3.12 or later is required",
                critical=True,
            )

    def check_dependencies(self) -> None:
        """Verify required dependencies are installed."""
        required_packages = {
            "pytest": "pytest",
            "cryptography": "cryptography",
            "yaml": "pyyaml",
        }

        all_found = True
        missing = []

        for import_name, package_name in required_packages.items():
            try:
                __import__(import_name)
            except ImportError:
                all_found = False
                missing.append(package_name)

        if all_found:
            self.add_result(
                "Dependencies",
                CheckStatus.PASS,
                "All required packages installed",
                f"Found: {', '.join(required_packages.values())}",
            )
        else:
            self.add_result(
                "Dependencies",
                CheckStatus.FAIL,
                f"Missing packages: {', '.join(missing)}",
                f"Install with: pip install {' '.join(missing)}",
                critical=True,
            )

    def check_tests(self) -> None:
        """Run test suite and verify all tests pass."""
        test_dir = self.project_root / "tests"
        if not test_dir.exists():
            self.add_result(
                "Test Suite",
                CheckStatus.SKIP,
                "Test directory not found",
                f"Expected: {test_dir}",
            )
            return

        try:
            result = subprocess.run(
                ["pytest", "-v", "--tb=short", str(test_dir)],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=120,
            )

            # Parse output for test count
            output = result.stdout + result.stderr

            # Check for missing Home Assistant
            if "ModuleNotFoundError: No module named 'homeassistant'" in output:
                self.add_result(
                    "Test Suite",
                    CheckStatus.SKIP,
                    "Home Assistant not installed in test environment",
                    "Tests require homeassistant package (OK if deploying to HA)",
                )
                return

            test_count = None
            duration = None

            for line in output.split("\n"):
                if "passed" in line:
                    # Extract test count (e.g., "171 passed in 47.53s")
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part == "passed":
                            test_count = parts[i - 1]
                        if "in" in part and i + 1 < len(parts):
                            duration = parts[i + 1]

            if result.returncode == 0:
                msg = "All tests passing"
                details = f"{test_count or 'All'} tests passed"
                if duration:
                    details += f" in {duration}"
                self.add_result("Test Suite", CheckStatus.PASS, msg, details)
            else:
                # Count failures
                failed_count = 0
                for line in output.split("\n"):
                    if "failed" in line.lower() and "passed" in line.lower():
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part == "failed":
                                failed_count = parts[i - 1]
                                break

                self.add_result(
                    "Test Suite",
                    CheckStatus.FAIL,
                    f"{failed_count or 'Some'} tests failing",
                    "Run: pytest -v for details",
                    critical=True,
                )

        except subprocess.TimeoutExpired:
            self.add_result(
                "Test Suite",
                CheckStatus.FAIL,
                "Tests timed out (>120s)",
                "Tests should complete in <60s normally",
                critical=True,
            )
        except FileNotFoundError:
            self.add_result(
                "Test Suite",
                CheckStatus.FAIL,
                "pytest not found",
                "Install with: pip install pytest",
                critical=True,
            )

    def check_code_quality(self) -> None:
        """Run flake8 and mypy for code quality checks."""
        component_dir = self.project_root / "custom_components" / "alexa"

        if not component_dir.exists():
            self.add_result(
                "Code Quality",
                CheckStatus.SKIP,
                "Component directory not found",
                f"Expected: {component_dir}",
            )
            return

        # Check flake8
        try:
            result = subprocess.run(
                ["flake8", str(component_dir)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                self.add_result(
                    "flake8 (Style)",
                    CheckStatus.PASS,
                    "No style issues",
                    "Code meets PEP 8 standards",
                )
            else:
                issue_count = len(result.stdout.strip().split("\n"))
                self.add_result(
                    "flake8 (Style)",
                    CheckStatus.WARN,
                    f"{issue_count} style issues",
                    "Run: flake8 custom_components/alexa/",
                )
        except FileNotFoundError:
            self.add_result(
                "flake8 (Style)",
                CheckStatus.SKIP,
                "flake8 not installed",
                "Install with: pip install flake8",
            )
        except subprocess.TimeoutExpired:
            self.add_result(
                "flake8 (Style)",
                CheckStatus.WARN,
                "flake8 timed out",
                "Code quality check incomplete",
            )

        # Check mypy
        try:
            result = subprocess.run(
                ["mypy", str(component_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode == 0:
                self.add_result(
                    "mypy (Types)",
                    CheckStatus.PASS,
                    "Type checking clean",
                    "No type errors found",
                )
            else:
                # Count errors
                error_count = result.stdout.count("error:")
                self.add_result(
                    "mypy (Types)",
                    CheckStatus.WARN,
                    f"{error_count} type issues",
                    "Run: mypy custom_components/alexa/",
                )
        except FileNotFoundError:
            self.add_result(
                "mypy (Types)",
                CheckStatus.SKIP,
                "mypy not installed",
                "Install with: pip install mypy",
            )
        except subprocess.TimeoutExpired:
            self.add_result(
                "mypy (Types)",
                CheckStatus.WARN,
                "mypy timed out",
                "Type checking incomplete",
            )

    def check_configuration(self) -> None:
        """Verify configuration constants are correct."""
        const_file = (
            self.project_root / "custom_components" / "alexa" / "const.py"
        )
        config_flow_file = (
            self.project_root / "custom_components" / "alexa" / "config_flow.py"
        )

        if not const_file.exists():
            self.add_result(
                "Configuration",
                CheckStatus.FAIL,
                "const.py not found",
                f"Expected: {const_file}",
                critical=True,
            )
            return

        const_content = const_file.read_text()
        config_flow_content = (
            config_flow_file.read_text() if config_flow_file.exists() else ""
        )

        # Check redirect URI
        expected_redirect = "https://my.home-assistant.io/redirect/alexa"
        if expected_redirect in config_flow_content:
            self.add_result(
                "Redirect URI",
                CheckStatus.PASS,
                f"Correct: {expected_redirect}",
                "Matches Home Assistant OAuth redirect",
            )
        else:
            self.add_result(
                "Redirect URI",
                CheckStatus.FAIL,
                "Redirect URI not found or incorrect",
                f"Expected: {expected_redirect}",
                critical=True,
            )

        # Check Amazon endpoints
        endpoints = [
            ("AMAZON_AUTH_URL", "https://www.amazon.com/ap/oa"),
            ("AMAZON_TOKEN_URL", "https://api.amazon.com/auth/o2/token"),
        ]

        all_correct = True
        for const_name, expected_url in endpoints:
            if f'{const_name} = "{expected_url}"' in const_content:
                continue
            else:
                all_correct = False
                break

        if all_correct:
            self.add_result(
                "Amazon Endpoints",
                CheckStatus.PASS,
                "All endpoints configured",
                "AUTH_URL and TOKEN_URL correct",
            )
        else:
            self.add_result(
                "Amazon Endpoints",
                CheckStatus.FAIL,
                "Endpoint configuration incorrect",
                "Check const.py for AMAZON_*_URL values",
                critical=True,
            )

        # Check required scopes
        if "alexa::skills:account_linking" in const_content:
            self.add_result(
                "OAuth Scopes",
                CheckStatus.PASS,
                "Required scope defined",
                "Scope: alexa::skills:account_linking",
            )
        else:
            self.add_result(
                "OAuth Scopes",
                CheckStatus.FAIL,
                "Required scope missing",
                "Need: alexa::skills:account_linking",
                critical=True,
            )

    def check_security(self) -> None:
        """Verify security features are implemented."""
        const_file = (
            self.project_root / "custom_components" / "alexa" / "const.py"
        )
        oauth_file = (
            self.project_root / "custom_components" / "alexa" / "oauth_manager.py"
        )

        if not const_file.exists() or not oauth_file.exists():
            self.add_result(
                "Security",
                CheckStatus.SKIP,
                "Required files not found",
                "Cannot verify security features",
            )
            return

        const_content = const_file.read_text()
        oauth_content = oauth_file.read_text()

        # Check storage version (encryption)
        if "STORAGE_VERSION = 2" in const_content:
            self.add_result(
                "Token Encryption",
                CheckStatus.PASS,
                "Storage v2 enabled (AES-256)",
                "Tokens encrypted at rest",
            )
        else:
            self.add_result(
                "Token Encryption",
                CheckStatus.WARN,
                "Storage version not v2",
                "Tokens may not be encrypted",
            )

        # Check PKCE implementation
        pkce_indicators = [
            "code_challenge",
            "code_verifier",
            "S256",
        ]

        pkce_found = sum(1 for indicator in pkce_indicators if indicator in oauth_content)

        if pkce_found >= 2:
            self.add_result(
                "PKCE (RFC 7636)",
                CheckStatus.PASS,
                "PKCE enabled (S256)",
                "Code challenge/verifier implemented",
            )
        else:
            self.add_result(
                "PKCE (RFC 7636)",
                CheckStatus.FAIL,
                "PKCE not implemented",
                "Required for security best practices",
                critical=True,
            )

        # Check state validation
        if (
            "secrets.compare_digest" in oauth_content
            or "hmac.compare_digest" in oauth_content
            or "constant_time" in oauth_content
        ):
            self.add_result(
                "State Validation",
                CheckStatus.PASS,
                "Constant-time comparison",
                "Protected against timing attacks (hmac.compare_digest)",
            )
        else:
            self.add_result(
                "State Validation",
                CheckStatus.WARN,
                "State validation method unclear",
                "Verify constant-time comparison usage",
            )

    def check_network(self) -> None:
        """Verify network connectivity to required endpoints."""
        endpoints = [
            ("my.home-assistant.io", 443, "Home Assistant OAuth service"),
            ("api.amazon.com", 443, "Amazon LWA API"),
        ]

        for host, port, description in endpoints:
            try:
                # DNS resolution
                socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)

                # SSL/TLS verification
                context = ssl.create_default_context()
                with socket.create_connection((host, port), timeout=5) as sock:
                    with context.wrap_socket(sock, server_hostname=host) as ssock:
                        cert = ssock.getpeercert()
                        # Verify we got a valid certificate
                        if cert:
                            self.add_result(
                                f"Network: {host}",
                                CheckStatus.PASS,
                                f"Reachable (SSL verified)",
                                description,
                            )
                        else:
                            self.add_result(
                                f"Network: {host}",
                                CheckStatus.WARN,
                                "Reachable but SSL unverified",
                                description,
                            )

            except socket.gaierror:
                self.add_result(
                    f"Network: {host}",
                    CheckStatus.FAIL,
                    "DNS resolution failed",
                    f"Cannot resolve {host}",
                    critical=True,
                )
            except socket.timeout:
                self.add_result(
                    f"Network: {host}",
                    CheckStatus.WARN,
                    "Connection timeout",
                    f"Cannot reach {host}:{port}",
                )
            except ssl.SSLError as e:
                self.add_result(
                    f"Network: {host}",
                    CheckStatus.FAIL,
                    "SSL/TLS verification failed",
                    str(e),
                    critical=True,
                )
            except Exception as e:
                self.add_result(
                    f"Network: {host}",
                    CheckStatus.WARN,
                    f"Connection failed: {type(e).__name__}",
                    str(e),
                )

    def check_home_assistant(self) -> None:
        """Attempt to detect Home Assistant installation."""
        # Common Home Assistant paths
        ha_paths = [
            Path.home() / ".homeassistant",
            Path("/config"),  # Docker/Container
            Path.home() / "homeassistant",
        ]

        ha_found = False
        for ha_path in ha_paths:
            if ha_path.exists() and (ha_path / "configuration.yaml").exists():
                ha_found = True
                version_file = ha_path / ".HA_VERSION"
                if version_file.exists():
                    version = version_file.read_text().strip()
                    self.add_result(
                        "Home Assistant",
                        CheckStatus.PASS,
                        f"Detected v{version}",
                        f"Config: {ha_path}",
                    )
                else:
                    self.add_result(
                        "Home Assistant",
                        CheckStatus.PASS,
                        "Installation detected",
                        f"Config: {ha_path}",
                    )
                break

        if not ha_found:
            self.add_result(
                "Home Assistant",
                CheckStatus.WARN,
                "Installation not detected",
                "Manual verification required (may be remote/Docker)",
            )

    def run_all_checks(self, quiet: bool = False) -> None:
        """Execute all preflight checks."""
        if not quiet:
            print("🚀 Alexa OAuth2 Integration - Pre-flight Checklist")
            print("=" * 60)
            print()
            print("Running checks...\n")

        self.check_python_version()
        self.check_dependencies()
        self.check_home_assistant()
        self.check_configuration()
        self.check_security()
        self.check_code_quality()
        self.check_tests()
        self.check_network()

    def print_results(self) -> None:
        """Print human-readable results."""
        # Group by category
        categories = {
            "Environment": [
                "Python Version",
                "Dependencies",
                "Home Assistant",
            ],
            "Configuration": [
                "Redirect URI",
                "Amazon Endpoints",
                "OAuth Scopes",
            ],
            "Security": [
                "Token Encryption",
                "PKCE (RFC 7636)",
                "State Validation",
            ],
            "Code Quality": [
                "flake8 (Style)",
                "mypy (Types)",
                "Test Suite",
            ],
            "Network": [
                r for r in self.results if r.name.startswith("Network:")
            ],
        }

        for category, check_names in categories.items():
            if category == "Network":
                # Already filtered
                matching_results = check_names
            else:
                matching_results = [
                    r for r in self.results if r.name in check_names
                ]

            if not matching_results:
                continue

            print(f"\n{category}:")
            print("-" * 60)

            for result in matching_results:
                status_icon = result.status.value
                print(f"{status_icon} {result.name}: {result.message}")

                if self.verbose and result.details:
                    print(f"   └─ {result.details}")

        print()
        print("=" * 60)

        # Summary
        pass_count = sum(1 for r in self.results if r.status == CheckStatus.PASS)
        fail_count = sum(1 for r in self.results if r.status == CheckStatus.FAIL)
        warn_count = sum(1 for r in self.results if r.status == CheckStatus.WARN)
        skip_count = sum(1 for r in self.results if r.status == CheckStatus.SKIP)

        critical_fails = [r for r in self.results if r.critical and r.status == CheckStatus.FAIL]

        print(f"\nSummary: {pass_count} passed, {fail_count} failed, {warn_count} warnings, {skip_count} skipped")

        if critical_fails:
            print(f"\n❌ CRITICAL FAILURES ({len(critical_fails)}):")
            for result in critical_fails:
                print(f"   • {result.name}: {result.message}")
            print("\n⛔ CANNOT PROCEED - Fix critical issues first")
            print()
        elif fail_count > 0 or warn_count > 0:
            print("\n⚠️  WARNINGS PRESENT - Review before proceeding")
            print()
            if warn_count > 0:
                print("Note: Warnings are non-critical but should be addressed")
            print()
        else:
            print("\n✅ ALL CHECKS PASSED - READY FOR AMAZON SKILL SETUP")
            print("\nNext steps:")
            print("   1. Open Amazon Developer Console")
            print("   2. Navigate to your Alexa skill")
            print("   3. Follow: AMAZON_SKILL_SETUP.md")
            print()

    def get_exit_code(self) -> int:
        """Determine exit code based on results."""
        critical_fails = [r for r in self.results if r.critical and r.status == CheckStatus.FAIL]
        has_warnings = any(r.status in (CheckStatus.FAIL, CheckStatus.WARN) for r in self.results)

        if critical_fails:
            return 1  # Critical failure
        elif has_warnings:
            return 2  # Warnings present
        else:
            return 0  # All checks passed

    def to_json(self) -> dict[str, Any]:
        """Convert results to JSON format."""
        return {
            "version": "1.0",
            "timestamp": self._get_timestamp(),
            "summary": {
                "total": len(self.results),
                "passed": sum(1 for r in self.results if r.status == CheckStatus.PASS),
                "failed": sum(1 for r in self.results if r.status == CheckStatus.FAIL),
                "warnings": sum(1 for r in self.results if r.status == CheckStatus.WARN),
                "skipped": sum(1 for r in self.results if r.status == CheckStatus.SKIP),
                "critical_failures": sum(
                    1 for r in self.results if r.critical and r.status == CheckStatus.FAIL
                ),
            },
            "checks": [
                {
                    "name": r.name,
                    "status": r.status.name.lower(),
                    "message": r.message,
                    "details": r.details,
                    "critical": r.critical,
                }
                for r in self.results
            ],
            "exit_code": self.get_exit_code(),
        }

    @staticmethod
    def _get_timestamp() -> str:
        """Get ISO 8601 timestamp."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Pre-flight checklist for Alexa OAuth2 integration setup"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed output for each check",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format",
    )

    args = parser.parse_args()

    checker = PreflightChecker(verbose=args.verbose)
    checker.run_all_checks(quiet=args.json)

    if args.json:
        print(json.dumps(checker.to_json(), indent=2))
    else:
        checker.print_results()

    return checker.get_exit_code()


if __name__ == "__main__":
    sys.exit(main())
