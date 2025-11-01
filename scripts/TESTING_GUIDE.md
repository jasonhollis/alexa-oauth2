# OAuth2 Testing Guide - Alexa Integration

This guide shows how to test the Alexa OAuth2 integration with real Amazon credentials using the verification script.

## Quick Start

### 1. Prerequisites

Before testing, ensure you have:

- ✅ Amazon Developer account
- ✅ Alexa Skill created in Amazon Developer Console
- ✅ Security Profile with OAuth settings configured
- ✅ Client ID and Client Secret from Amazon
- ✅ Redirect URI registered: `https://my.home-assistant.io/redirect/alexa`
- ✅ Network access to Amazon APIs

### 2. Activate Test Environment

```bash
cd /Users/jason/projects/alexa-oauth2
source venv-test/bin/activate
```

### 3. Run Security Audit (No Credentials Needed)

Quick security check without requiring Amazon credentials:

```bash
python scripts/verify_oauth.py --security-audit
```

**Expected Output**:
- ✅ No hardcoded credentials
- ✅ PKCE using secrets.token_bytes + SHA-256
- ✅ State validation using hmac.compare_digest
- ✅ Token storage using Home Assistant Store (encrypted)
- ⚠️ Some token logging warnings (non-critical)
- ✅ All URLs use HTTPS

### 4. Run Full Verification (Requires Credentials)

Complete OAuth flow testing with your Amazon Developer credentials:

```bash
python scripts/verify_oauth.py --verbose
```

## Step-by-Step OAuth Testing

### Phase 1: Pre-flight Checks

The script will:

1. **Verify Project Structure**
   - Checks all required files exist
   - Validates imports work

2. **Verify OAuth Constants**
   - Validates Amazon endpoint URLs
   - Checks required scopes

3. **Collect Credentials**
   ```
   Enter your Amazon Developer credentials:
   Client ID (amzn1.application-oa2-client.*): [paste from Amazon Console]
   Client Secret: [paste from Amazon Console]
   ```

4. **Verify Redirect URI**
   - Confirms `https://my.home-assistant.io/redirect/alexa` is registered
   - You must answer "yes" to proceed

**Expected**: ✅ All pre-flight checks pass

### Phase 2: PKCE Verification

The script automatically tests:

1. **PKCE Pair Generation**
   - Generates code_verifier (43 chars)
   - Generates code_challenge (43 chars SHA-256)

2. **Challenge Computation**
   - Verifies: challenge = BASE64URL(SHA256(verifier))

3. **Randomness**
   - Ensures different PKCE pairs each time

4. **State Parameter**
   - Generates 43-char random state
   - Tests constant-time validation

**Expected**: ✅ All PKCE tests pass

### Phase 3: Authorization URL Generation

The script:

1. **Generates Authorization URL**
   ```
   https://www.amazon.com/ap/oa?
     client_id=amzn1.application-oa2-client.xxx&
     response_type=code&
     scope=alexa::skills:account_linking&
     redirect_uri=https://my.home-assistant.io/redirect/alexa&
     state=xxx&
     code_challenge=xxx&
     code_challenge_method=S256
   ```

2. **Validates All Parameters**
   - client_id matches your credentials
   - response_type = "code"
   - scope = "alexa::skills:account_linking"
   - redirect_uri matches registered URI
   - state parameter present
   - code_challenge present
   - code_challenge_method = "S256"

**Expected**: ✅ URL generated with all required parameters

### Phase 4: OAuth Flow Walkthrough

#### Step 1: Open Authorization URL

The script displays:
```
Copy this URL and open it in your browser:
https://www.amazon.com/ap/oa?client_id=...

Press Enter when ready...
```

**Actions**:
1. Copy the URL
2. Open in browser
3. Press Enter

#### Step 2: Authorize on Amazon

**What happens**:
1. Browser opens Amazon login page
2. Log in with your Amazon account
3. Amazon shows authorization page
4. Click "Allow" to authorize

**Screenshot**: You should see "Allow [Your App] to access your Amazon account?"

#### Step 3: Copy Redirect URL

**Important**: After clicking "Allow", Amazon will redirect to:
```
https://my.home-assistant.io/redirect/alexa?code=ANaRx...&state=xyz...
```

**This redirect will fail** (Home Assistant not running) - **that's OK!**

**Actions**:
1. Browser shows "This site can't be reached" or similar
2. Copy the **ENTIRE URL** from browser address bar
3. Paste into script when prompted

**Example URL to copy**:
```
https://my.home-assistant.io/redirect/alexa?code=ANaRxDaHBpGQltBmDqGFTr&state=xyzABC123DEF456...
```

#### Step 4: State Validation

The script automatically:
1. Extracts `code` and `state` from URL
2. Validates state matches (CSRF protection)
3. Checks for error parameters

**Expected**: ✅ State validation passes

#### Step 5: Token Exchange

The script:
1. Sends POST to `https://api.amazon.com/auth/o2/token`
2. Includes authorization code
3. Includes PKCE code_verifier
4. Exchanges for access and refresh tokens

**Expected**: ✅ Tokens received successfully

### Phase 5: Token Verification

The script validates received tokens:

1. **Required Fields**
   - access_token present ✓
   - refresh_token present ✓
   - token_type present ✓
   - expires_in present ✓

2. **Token Type**
   - token_type = "Bearer" ✓

3. **Access Token Format**
   - Starts with "Atza|" ✓
   - Length > 100 characters ✓

4. **Refresh Token Format**
   - Starts with "Atzr|" ✓
   - Length > 100 characters ✓

5. **Expiry**
   - expires_in is positive integer ✓
   - Typically 3600 seconds (1 hour) ✓

6. **Scope**
   - scope includes "alexa::skills:account_linking" ✓

**Expected**: ✅ All token validations pass

### Phase 6: Token Refresh (Optional)

If you run with `--test-refresh`:

```bash
python scripts/verify_oauth.py --test-refresh
```

The script will:
1. Use refresh_token to get new access_token
2. Verify new access_token is different
3. Check if refresh_token rotated

**Expected**: ✅ Token refresh successful, new access_token received

### Phase 7: Security Audit

Final security check:
1. ✅ No hardcoded credentials found
2. ✅ PKCE using cryptographic randomness
3. ✅ State validation using constant-time comparison
4. ✅ Token storage encrypted
5. ⚠️ Token logging warnings (informational)
6. ✅ All URLs use HTTPS

**Expected**: ✅ 5-6 security checks pass

## Test Results Summary

### Success Output

```
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

### What This Means

✅ **OAuth Implementation Verified**: Your code correctly implements OAuth2 + PKCE

✅ **Amazon Integration Working**: Successfully authenticated with real Amazon APIs

✅ **Token Management Functional**: Access and refresh tokens working correctly

✅ **Security Best Practices**: PKCE, state validation, encrypted storage all confirmed

✅ **Production Ready**: Integration can be deployed with confidence

## Troubleshooting Common Issues

### Issue 1: "Invalid Client ID Format"

**Error**:
```
✗ Client ID format: Client ID should start with 'amzn1.application-oa2-client.'
```

**Fix**:
1. Go to Amazon Developer Console
2. Navigate to your Security Profile
3. Copy the **exact** Client ID
4. Ensure you copied the complete string (typically 80+ chars)

### Issue 2: "Redirect URI Not Registered"

**Error**:
```
✗ Redirect URI: Redirect URI not registered
```

**Fix**:
1. Go to Amazon Developer Console → Security Profile → Web Settings
2. Add to "Allowed Return URLs":
   ```
   https://my.home-assistant.io/redirect/alexa
   ```
3. **Exact match required** (no trailing slash, HTTPS, case-sensitive)
4. Click "Save"
5. Wait a few minutes for changes to propagate

### Issue 3: "Authorization Code Expired"

**Error**:
```
✗ Token exchange: Invalid authorization code: The authorization code is invalid
```

**Cause**: Authorization codes expire in 10 minutes

**Fix**:
1. Start OAuth flow again (generate new authorization URL)
2. Complete authorization within 10 minutes
3. Copy and paste callback URL immediately

### Issue 4: "State Mismatch"

**Error**:
```
✗ State validation: State parameter mismatch (CSRF protection triggered)
```

**Possible Causes**:
- Used old/cached authorization URL
- Browser cached redirect
- Pasted wrong callback URL

**Fix**:
1. Clear browser cache
2. Generate fresh authorization URL
3. Complete OAuth flow without refreshing
4. Copy callback URL from address bar (not history)

### Issue 5: "Invalid Grant (Refresh Token)"

**Error**:
```
✗ Token refresh: Refresh token invalid: The refresh token is invalid
```

**Cause**: Refresh token expired (typically after 1 year) or revoked

**Fix**:
- This is expected behavior after long period
- In production, triggers reauth flow
- For testing, restart OAuth flow

### Issue 6: "Network Error"

**Error**:
```
✗ Token exchange: Network error: Connection timeout
```

**Fix**:
1. Check internet connectivity
2. Verify Amazon API is accessible: `curl https://api.amazon.com`
3. Check firewall/proxy settings
4. Try again (transient network issues)

## Production Testing Workflow

### Before Deployment

1. **Run Security Audit**
   ```bash
   python scripts/verify_oauth.py --security-audit
   ```
   - Must pass all critical security checks

2. **Run Full Verification**
   ```bash
   python scripts/verify_oauth.py --verbose --test-refresh
   ```
   - Test complete OAuth flow
   - Verify token refresh works

3. **Run Unit Tests**
   ```bash
   pytest tests/ -v
   ```
   - Ensure all 171 tests pass

### After Deployment

1. **Monitor First OAuth Flow**
   - Watch Home Assistant logs
   - Verify tokens saved to storage
   - Confirm integration loads

2. **Test Refresh**
   - Wait for token to near expiry
   - Verify automatic refresh works

3. **Test Reauth**
   - Simulate expired refresh token
   - Verify reauth flow triggers
   - Confirm user can reauthorize

## Advanced Testing

### Testing Regional Endpoints

For EU/FE Amazon regions, the script can be modified to test:
- EU: `https://www.amazon.co.uk/ap/oa`
- FE: `https://www.amazon.co.jp/ap/oa`

### Testing Error Conditions

1. **Invalid Credentials**
   - Use wrong client_secret
   - Expect: `invalid_client` error

2. **Wrong Redirect URI**
   - Use different redirect_uri
   - Expect: `redirect_uri_mismatch` error

3. **Expired Code**
   - Wait >10 minutes before pasting callback URL
   - Expect: `invalid_grant` error

4. **Wrong Scope**
   - Request different scope
   - Expect: `invalid_scope` error

### Performance Testing

Monitor OAuth flow timing:
- Authorization URL generation: <1ms
- Token exchange: <1000ms (network dependent)
- Token refresh: <1000ms (network dependent)

## FAQ

### Q: Do I need Home Assistant running to test?

**A**: No, the verification script has its own mock environment. You only need the test virtual environment activated.

### Q: Are my credentials saved?

**A**: No, credentials are only used during the test and are not saved to disk.

### Q: Can I test without real credentials?

**A**: Security audit (`--security-audit`) works without credentials. Full OAuth flow requires real Amazon credentials.

### Q: What if I don't have an Alexa Skill?

**A**: You can create a test skill in Amazon Developer Console just for testing OAuth. No need to publish it.

### Q: How long are tokens valid?

**A**:
- Access tokens: 1 hour (3600 seconds)
- Refresh tokens: ~1 year (can be revoked anytime)

### Q: Can I test token expiry?

**A**: The script tests token refresh, which simulates near-expiry. For actual expiry testing, you'd need to wait 1 hour or mock the system clock.

### Q: What about token revocation?

**A**: Token revocation is tested in unit tests. The verification script focuses on OAuth flow and token refresh.

## Continuous Integration

### GitHub Actions Example

```yaml
name: OAuth Security Audit

on: [push, pull_request]

jobs:
  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.12'
      - name: Install dependencies
        run: |
          python -m venv venv-test
          source venv-test/bin/activate
          pip install -r requirements-test.txt
      - name: Run security audit
        run: |
          source venv-test/bin/activate
          python scripts/verify_oauth.py --security-audit
```

### Pre-commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit

echo "Running OAuth security audit..."
source venv-test/bin/activate
python scripts/verify_oauth.py --security-audit

if [ $? -ne 0 ]; then
    echo "Security audit failed. Fix issues before committing."
    exit 1
fi
```

## Conclusion

The verification script provides comprehensive testing of:
- ✅ OAuth2 implementation correctness
- ✅ PKCE security (RFC 7636)
- ✅ Token management
- ✅ Amazon API integration
- ✅ Security best practices

**Recommended**: Run full verification with real credentials before any production deployment.

---

**Need Help?**
- Check error messages and suggestions in script output
- Review "Common OAuth Error Diagnosis" section
- Consult Amazon Developer Console documentation
- Check Home Assistant OAuth integration docs
