# OAuth Troubleshooting Guide - Alexa Integration

**Created**: 2025-11-01
**Issue**: Client ID and Secret not working after updating to commit 20c8f2f

## Quick Diagnostic Commands (Run on Home Assistant)

```bash
# 1. Check if integration is updated
ls -la /config/custom_components/alexa/
cat /config/custom_components/alexa/const.py | grep "REQUIRED_SCOPES"
# Expected: REQUIRED_SCOPES = "profile:user_id"

# 2. Check external_url configuration
grep -i "external_url" /config/configuration.yaml
# Expected: https://0gdzommh4w1tug2s97xnn9spylzmkkbs.ui.nabu.casa

# 3. Check for OAuth errors in logs
tail -100 /config/home-assistant.log | grep -i "alexa\|oauth\|redirect"

# 4. Verify config_flow has new redirect URI method
grep "_get_redirect_uri" /config/custom_components/alexa/config_flow.py
# Expected: Should find the method definition

# 5. Check what Home Assistant sees as external_url
grep "external_url" /config/.storage/core.config
```

## Common Issues and Fixes

### Issue 1: Integration Not Updated (Still Old Version)

**Symptom**: OAuth scope errors, `my.home-assistant.io` redirect errors

**Fix**:
```bash
# Via HACS UI
1. HACS → Integrations → Alexa OAuth2
2. Click "Redownload"
3. Select latest version (commit 20c8f2f or later)
4. Restart Home Assistant

# Or manually via Git
cd /config/custom_components/
rm -rf alexa/
git clone https://github.com/jasonhollis/alexa-oauth2.git alexa
cd alexa
git checkout 20c8f2f
```

**Verify Fix**:
```bash
cat /config/custom_components/alexa/const.py | grep "REQUIRED_SCOPES"
# Must show: REQUIRED_SCOPES = "profile:user_id"
```

### Issue 2: Redirect URI Not Added to Amazon

**Symptom**: OAuth works, Amazon login works, but redirect fails with "Page not found"

**Fix**:
1. Go to: https://developer.amazon.com/loginwithamazon/console/site/lwa/overview.html
2. Click "Home Assistant AlexaProfile"
3. Click "Web Settings" tab
4. Add to "Allowed Return URLs":
   ```
   https://0gdzommh4w1tug2s97xnn9spylzmkkbs.ui.nabu.casa/auth/external/callback
   ```
5. Click "Save"

**Verify Fix**:
- Check Amazon console shows the new URL
- Try OAuth flow again

### Issue 3: external_url Not Configured

**Symptom**: Error message about `external_url` not configured

**Fix**:
```yaml
# In /config/configuration.yaml, add:
homeassistant:
  external_url: https://0gdzommh4w1tug2s97xnn9spylzmkkbs.ui.nabu.casa
```

**Then restart Home Assistant**

### Issue 4: Wrong OAuth Scope in Amazon

**Symptom**: Amazon returns "400 Bad Request - unknown scope"

**Fix**:
1. Go to Amazon Developer Console → Alexa → Skills
2. Find your "Home Assistant Alexa" skill
3. Click "Account Linking"
4. Verify scope is: `profile:user_id` (NOT `smart_home` or `alexa::skills:account_linking`)
5. Save changes

### Issue 5: Client ID/Secret Mismatch

**Symptom**: "Invalid client" errors

**Credentials**: See CREDENTIALS.txt file (not committed to git)
- **Client ID**: `amzn1.application-oa2-client.[redacted]`
- **Client Secret**: `amzn1.oa2-cs.v1.[redacted]`

**Verify**: These match what's in Amazon Developer Console → Login with Amazon → Security Profiles

## Expected OAuth Flow

**Correct Flow** (after fixes):

1. User adds integration in HA
2. Enters Client ID and Secret
3. Clicks authorization link
4. Redirects to Amazon login (`https://www.amazon.com/ap/oa?...`)
5. User logs in to Amazon
6. Amazon asks permission: "Allow Home Assistant AlexaProfile to access your profile?"
7. User clicks "Allow"
8. Amazon redirects to: `https://0gdzommh4w1tug2s97xnn9spylzmkkbs.ui.nabu.casa/auth/external/callback?code=...&state=...`
9. Home Assistant exchanges code for tokens
10. Integration shows as "Configured" ✓

## Debugging OAuth Errors

### Enable Debug Logging

Add to `/config/configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.alexa: debug
    custom_components.alexa.oauth_manager: debug
    custom_components.alexa.config_flow: debug
```

Restart Home Assistant, then check logs:
```bash
tail -f /config/home-assistant.log | grep "custom_components.alexa"
```

### Common Error Messages

**"400 Bad Request - unknown scope"**
→ Amazon doesn't recognize the OAuth scope
→ Fix: Update Amazon Account Linking scope to `profile:user_id`

**"Page not found" (Netlify)**
→ Redirect URI not whitelisted in Amazon
→ Fix: Add Nabu Casa URL to Amazon LWA Allowed Return URLs

**"external_url not configured"**
→ Home Assistant doesn't know its public URL
→ Fix: Add `external_url` to configuration.yaml

**"Invalid client"**
→ Client ID or Secret doesn't match Amazon
→ Fix: Double-check credentials in Amazon Developer Console

## Testing OAuth Flow

```bash
# Watch logs in real-time
ssh root@haboxhill.local
tail -f /config/home-assistant.log | grep -i "alexa\|oauth"

# Then try adding integration via HA UI
# Look for these log messages:
# - "Generated authorization URL"
# - "State saved for flow"
# - "Received OAuth callback"
# - "Code exchange successful"
# - "Tokens saved to storage"
```

## Files Modified (Commit 20c8f2f)

1. `custom_components/alexa/const.py`:
   - Changed `REQUIRED_SCOPES` from `alexa::skills:account_linking` to `profile:user_id`

2. `custom_components/alexa/config_flow.py`:
   - Removed hardcoded `HA_OAUTH_REDIRECT_URI = "https://my.home-assistant.io/redirect/alexa"`
   - Added `_get_redirect_uri()` method using `hass.config.external_url`
   - Updated 3 OAuth flows to use dynamic redirect URI

3. `custom_components/alexa/token_manager.py`:
   - Updated docstrings to show correct scope

4. `custom_components/alexa/oauth_manager.py`:
   - Updated docstrings to show correct scope

## V2 Deployment Status (2025-11-01 23:35)

### What Happened
1. ✓ Committed V2 using AbstractOAuth2FlowHandler (commit 721f43e)
2. ✓ HACS failed to download (tried commit hash as branch - 404 error)
3. ✓ Bypassed HACS, copied files directly via rsync
4. ✓ Deleted old V1 files (oauth_manager.py, etc.) that weren't removed by rsync
5. ✓ Cleared Python bytecode cache (__pycache__)
6. ✓ Restarted Home Assistant
7. ✓ V2 loaded successfully without errors

### Current Status
- **Integration loads**: No errors in logs ✓
- **Files deployed**: Only V2 files present ✓
- **Integration appears in UI**: **UNTESTED** - need to check if "Amazon Alexa" shows in Add Integration search

### Hybrid Config Flow Fix (2025-11-01 23:45)

**Problem Found**: `missing_configuration` error when clicking integration
- AbstractOAuth2FlowHandler needs OAuth implementation registered BEFORE config flow
- But we need user's client_id/client_secret to create the implementation
- This is the "chicken-and-egg problem" predicted by strategic consultant

**Solution Implemented**: Hybrid config flow
1. `async_step_user` now shows form to collect client_id and client_secret
2. After user submits, we register AlexaOAuth2Implementation with those credentials
3. Then proceed with standard OAuth flow via `async_step_pick_implementation`
4. Created `translations/en.json` for form labels

**Files Modified**:
- `config_flow.py`: Added credential form before OAuth
- `translations/en.json`: Form labels and descriptions

**Deployed**: Restarted HA with hybrid flow

**Bug Found** (2025-11-01 23:48): Missing `await` on `async_get_implementations()`
- Error: `TypeError: argument of type 'coroutine' is not iterable`
- Line 143 in config_flow.py
- **Fix**: Added `await` before `config_entry_oauth2_flow.async_get_implementations()`
- **Deployed**: Restarted HA with bugfix

**Bug Found** (2025-11-01 23:59): Missing `_async_refresh_token` method
- Error: `TypeError: Can't instantiate abstract class AlexaOAuth2Implementation without an implementation for abstract method '_async_refresh_token'`
- HA framework now requires `_async_refresh_token` (underscore prefix) in addition to `async_refresh_token`
- **Fix**: Added `_async_refresh_token` method in oauth.py that delegates to `async_refresh_token`
- **Deployed**: Restarted HA with bugfix

**Bug Found** (2025-11-02 00:15): Constructor signature mismatch
- Error: `TypeError: object.__init__() takes exactly one argument (the instance to initialize)` at line 66
- Root cause: `AbstractOAuth2Implementation` has no `__init__` method, shouldn't call `super().__init__()` with parameters
- **Fix**: Removed `super().__init__()` call entirely, initialize all instance variables directly
- **Deployed**: Restarted HA with bugfix

**Bug Found** (2025-11-02 00:20): Token exchange not implemented
- Error: `super().async_resolve_external_data()` calls abstract method (not implemented in parent)
- Root cause: Must implement entire token exchange ourselves, not delegate to parent
- **Fix**: Implemented complete token exchange with HTTP POST to Amazon token endpoint
- Includes PKCE `code_verifier` in token request
- **Deployed**: Restarted HA with bugfix

**Bug Found** (2025-11-02 00:25): Token refresh delegation issue
- Root cause: `async_refresh_token` was delegating to parent's method which doesn't exist
- **Fix**: Implemented `_async_refresh_token` directly with HTTP POST to Amazon token endpoint
- Uses standard OAuth2 refresh flow (no PKCE for refresh)
- **Deployed**: Cleaned __pycache__, rsynced all V2 files, restarted HA (2025-11-02 00:30)

**Bug Found** (2025-11-02 00:49): OAuth callback routing issue
- Error: "Invalid state. Is My Home Assistant configured to go to the right instance?"
- Symptom: OAuth authorization URL generated correctly, user authorizes on Amazon, Amazon redirects back with code and state, but callback never reaches config flow
- Root cause: Calling `async_step_pick_implementation()` doesn't properly establish external step for callback routing
- **Fix**: Set `self.flow_impl` directly and call `async_step_auth()` instead of `async_step_pick_implementation()`
- Changed lines 160-167 in config_flow.py:
  ```python
  # Get the implementation and set it as flow_impl
  implementations = await config_entry_oauth2_flow.async_get_implementations(
      self.hass, DOMAIN
  )
  self.flow_impl = implementations[DOMAIN]

  # Now proceed directly to auth step (bypassing pick_implementation)
  return await self.async_step_auth()
  ```
- **Deployed**: Rsynced config_flow.py, cleaned __pycache__, restarted HA (2025-11-02 00:49)

### Deployment Verification (2025-11-02 00:30)

**Files on Server** (after all bugfixes):
```bash
/config/custom_components/alexa/
├── __init__.py          # V2 (13KB)
├── config_flow.py       # V2 with hybrid flow (10KB)
├── const.py             # V2 (2.9KB)
├── oauth.py             # V2 with all fixes (12KB)
├── manifest.json        # V2 (426 bytes)
└── translations/en.json # V2 form labels
```

**Changes Made**:
1. Removed invalid `super().__init__()` call
2. Implemented complete token exchange with PKCE
3. Implemented token refresh properly
4. All V1 files removed (oauth_manager.py, session_manager.py, token_manager.py)
5. Cleared Python bytecode cache

### Next Test
**Try integration again**:
1. Open Home Assistant UI
2. Settings → Devices & Services → Add Integration
3. Search for "Alexa" → click "Amazon Alexa"
4. Expected: Form asking for Client ID and Client Secret
5. Submit credentials → should redirect to Amazon OAuth
6. After Amazon authorization → integration should be configured

## Next Steps After Fixing

1. ✓ Verify OAuth flow completes successfully
2. ✓ Check tokens are saved in `.storage/`
3. ✓ Test Alexa voice commands
4. ✓ Verify token refresh works (check logs after 1 hour)
5. ✓ Document any remaining issues

## Support Resources

- **GitHub Repo**: https://github.com/jasonhollis/alexa-oauth2
- **Commit with fixes**: 20c8f2f
- **Credentials**: `/Users/jason/projects/alexa-oauth2/CREDENTIALS.txt`
- **Amazon Console**: https://developer.amazon.com/loginwithamazon/console/site/lwa/overview.html
