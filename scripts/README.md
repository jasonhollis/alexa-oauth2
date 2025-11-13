# OAuth2 Verification Scripts

This directory contains verification and testing scripts for the Alexa OAuth2 integration.

## verify_oauth.py

Comprehensive OAuth2 verification and testing script for validating the integration with real Amazon Alexa credentials.

### Features

- **Pre-flight Checks**: Verifies project structure, constants, credentials format, and redirect URI
- **PKCE Verification**: Tests PKCE implementation (code_verifier/code_challenge generation and validation)
- **OAuth Flow Walkthrough**: Interactive step-by-step OAuth flow testing
- **Token Verification**: Validates token format, expiry, and scopes
- **Token Refresh Testing**: Tests token refresh functionality
- **Security Audit**: Scans for hardcoded credentials, PKCE security, token storage security
- **Error Diagnosis**: Provides actionable suggestions for common OAuth errors

### Requirements

- Home Assistant test environment (venv-test)
- Valid Amazon Developer credentials
- Network connectivity to Amazon APIs

### Usage

#### Full Verification (Interactive)

Tests complete OAuth flow with real Amazon credentials:

```bash
cd /Users/jason/projects/alexa-oauth2
source venv-test/bin/activate
python scripts/verify_oauth.py
```

This will:
1. Verify project structure and constants
2. Collect Amazon Developer credentials
3. Verify PKCE implementation
4. Generate authorization URL
5. Guide you through OAuth flow
6. Exchange code for tokens
7. Verify token format
8. Run security audit

#### Pre-flight Check Only

Verify setup without running OAuth flow:

```bash
python scripts/verify_oauth.py --check-only
```

#### Security Audit Only

Run security audit without OAuth flow:

```bash
python scripts/verify_oauth.py --security-audit
```

#### Token Refresh Testing

Test token refresh after OAuth flow:

```bash
python scripts/verify_oauth.py --test-refresh
```

#### Verbose Output

Show detailed information:

```bash
python scripts/verify_oauth.py --verbose
```

#### Debug Logging

Enable debug logging:

```bash
python scripts/verify_oauth.py --debug
```

### What Gets Tested

#### Pre-flight Checks

1. **Project Structure**: Verifies all required files exist
   - `custom_components/alexa/__init__.py`
   - `custom_components/alexa/config_flow.py`
   - `custom_components/alexa/oauth_manager.py`
   - `custom_components/alexa/token_manager.py`

2. **OAuth Constants**: Validates configuration
   - `AMAZON_AUTH_URL` is HTTPS
   - `AMAZON_TOKEN_URL` is HTTPS
   - `REQUIRED_SCOPES` = "alexa::skills:account_linking"

3. **Credentials Format**: Validates Amazon Developer credentials
   - Client ID starts with "amzn1.application-oa2-client."
   - Client ID length >= 50 characters
   - Client Secret length >= 32 characters

4. **Redirect URI**: Confirms redirect URI registered
   - `https://my.home-assistant.io/redirect/alexa`

#### PKCE Verification

1. **PKCE Pair Generation**: Tests code_verifier and code_challenge generation
   - Verifier length = 43 characters (32 bytes base64url)
   - Challenge length = 43 characters (32 bytes SHA-256 base64url)

2. **Challenge Computation**: Verifies challenge = BASE64URL(SHA256(verifier))

3. **Randomness**: Ensures different PKCE pairs on each generation

4. **State Parameter**: Tests state generation and validation
   - State length = 43 characters (32 bytes base64url)
   - State randomness verified
   - Constant-time comparison used (hmac.compare_digest)

#### Authorization URL Verification

1. **URL Generation**: Tests authorization URL creation

2. **URL Components**: Validates all required parameters
   - `client_id`
   - `response_type` = "code"
   - `scope` = "alexa::skills:account_linking"
   - `redirect_uri`
   - `state`
   - `code_challenge`
   - `code_challenge_method` = "S256"

3. **Parameter Matching**: Verifies URL parameters match internal values

#### OAuth Flow Walkthrough

1. **Authorization**: Guides user through Amazon authorization

2. **Callback Handling**: Parses callback URL and extracts parameters

3. **State Validation**: Validates state parameter (CSRF protection)

4. **Token Exchange**: Exchanges authorization code for tokens

#### Token Verification

1. **Required Fields**: Checks for access_token, refresh_token, token_type, expires_in

2. **Token Type**: Validates token_type = "Bearer"

3. **Access Token Format**: Verifies Amazon format (starts with "Atza|", length > 100)

4. **Refresh Token Format**: Verifies Amazon format (starts with "Atzr|", length > 100)

5. **Expiry**: Validates expires_in is positive integer

6. **Scope**: Verifies granted scope matches requested

#### Token Refresh Testing

1. **Refresh Request**: Tests token refresh with refresh_token

2. **New Access Token**: Verifies new access token received

3. **Refresh Token Rotation**: Notes if refresh token rotated

#### Security Audit

1. **Hardcoded Credentials**: Scans for hardcoded secrets in source code
   - client_secret
   - access_token
   - refresh_token

2. **PKCE Security**: Verifies cryptographic implementations
   - Uses `secrets.token_bytes()` for randomness
   - Uses `hashlib.sha256()` for challenge

3. **State Validation**: Checks constant-time comparison
   - Uses `hmac.compare_digest()` to prevent timing attacks

4. **Token Storage**: Verifies encrypted storage
   - Uses Home Assistant `Store` class (encrypted)

5. **Token Logging**: Checks for token exposure in log statements

6. **HTTPS Enforcement**: Verifies all URLs use HTTPS

### Output Format

The script provides color-coded output:

- 🟢 **Green (✓)**: Test passed
- 🔴 **Red (✗)**: Test failed
- 🟡 **Yellow (⚠)**: Warning
- 🔵 **Blue (ℹ)**: Information
- **Bold**: Section headers and steps

### Error Diagnosis

If tests fail, the script provides:

1. **Error Message**: Clear description of what failed
2. **Suggestion**: Actionable fix for the issue
3. **Common Errors**: Diagnosis guide for common OAuth errors

#### Common OAuth Errors Covered

1. `invalid_client` - Invalid credentials
2. `invalid_grant` (authorization code) - Code expired/used
3. `invalid_grant` (refresh token) - Token expired/revoked
4. `redirect_uri_mismatch` - URI not registered
5. `invalid_scope` - Scope not enabled
6. `State mismatch` - CSRF protection triggered
7. `PKCE verification failed` - Verifier/challenge mismatch

### Exit Codes

- **0**: All tests passed
- **1**: One or more tests failed
- **130**: Interrupted by user (Ctrl+C)

### Example Output

```
╔════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║            OAuth2 Verification & Testing - Alexa Integration              ║
║                                                                            ║
╚════════════════════════════════════════════════════════════════════════════╝

================================================================================
                              Pre-Flight Checks
================================================================================

[Step 1] Verifying project structure...
✓ Project structure: All required files present

[Step 2] Verifying OAuth constants...
✓ OAuth constants: All constants properly configured

[Step 3] Gathering Amazon Developer credentials...
Enter your Amazon Developer credentials:
(These are found in your Amazon Developer Console)
(They will NOT be saved to disk)

Client ID (amzn1.application-oa2-client.*): amzn1.application-oa2-client.abc123...
Client Secret: ***

✓ Credentials format: Client ID and Secret format valid

[Step 4] Verifying redirect URI configuration...
ℹ    Redirect URI: https://my.home-assistant.io/redirect/alexa
⚠    Make sure this EXACT URI is registered in Amazon Developer Console
⚠    under 'Allowed Return URLs' in your Security Profile

Is 'https://my.home-assistant.io/redirect/alexa' registered? (yes/no): yes
✓ Redirect URI: Confirmed registered in Amazon Developer Console

================================================================================
                              PKCE Verification
================================================================================

[Step 1] Testing PKCE pair generation...
✓ PKCE pair generation: Generated valid verifier and challenge (both 43 chars)

[Step 2] Verifying PKCE challenge computation...
✓ PKCE challenge computation: Challenge correctly computed as SHA-256 of verifier

[Step 3] Verifying PKCE randomness...
✓ PKCE randomness: PKCE pairs are properly randomized

... (more tests)

================================================================================
                                Test Summary
================================================================================

Total Tests: 25
✓ Passed: 25

╔════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║                    ✓ ALL TESTS PASSED SUCCESSFULLY!                       ║
║                                                                            ║
║      Your OAuth2 implementation is working correctly with real            ║
║      Amazon credentials. You can now use this integration in              ║
║      production.                                                           ║
║                                                                            ║
╚════════════════════════════════════════════════════════════════════════════╝
```

### Integration with CI/CD

The script can be used in CI/CD pipelines:

```bash
# Security audit in CI (no credentials needed)
python scripts/verify_oauth.py --security-audit
```

### Notes

- **Credentials**: Not saved to disk, only used for testing
- **Network**: Requires internet access to Amazon APIs
- **Interactive**: OAuth flow requires user interaction (opening browser)
- **Non-destructive**: Only reads tokens, doesn't modify integration

### Troubleshooting

#### ImportError: No module named 'homeassistant'

Make sure you've activated the test virtual environment:

```bash
source venv-test/bin/activate
```

#### Network errors

Check:
1. Internet connectivity
2. Amazon API availability (https://api.amazon.com)
3. Firewall/proxy settings

#### Authorization code expired

Authorization codes expire in 10 minutes. If you see "invalid_grant" error:
1. Start OAuth flow again
2. Complete authorization within 10 minutes

#### Redirect URI mismatch

Ensure exact match in Amazon Developer Console:
- URL: `https://my.home-assistant.io/redirect/alexa`
- No trailing slash
- HTTPS (not HTTP)
- Case-sensitive

### Contributing

When modifying the OAuth implementation:

1. Run full verification: `python scripts/verify_oauth.py --verbose`
2. Run security audit: `python scripts/verify_oauth.py --security-audit`
3. Ensure all existing tests pass: `pytest tests/`

### Related Files

- `/custom_components/alexa/oauth_manager.py` - OAuth implementation
- `/custom_components/alexa/token_manager.py` - Token management
- `/custom_components/alexa/config_flow.py` - Config flow (uses OAuth)
- `/tests/components/alexa/test_oauth_manager.py` - OAuth unit tests

### Support

For issues with the verification script:
1. Check this README
2. Review error suggestions in output
3. Check "Common OAuth Error Diagnosis" section in script output

For OAuth implementation issues:
1. Review test failures and suggestions
2. Check Amazon Developer Console configuration
3. Verify network connectivity
4. Review Home Assistant logs
