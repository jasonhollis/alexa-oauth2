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
