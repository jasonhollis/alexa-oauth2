# Grok Strategic Consultant Analysis Summary

**Date**: 2025-11-02
**Updated**: 2025-11-02 15:55 UTC
**Issue**: "Invalid state" error in OAuth2 callback - **ROOT CAUSE FOUND!**

## ACTUAL Root Cause: Config Flow Timeout

The "Invalid state" error is caused by **config flow expiration**, NOT JWT validation issues!

**Evidence from testing**:
- Direct curl test to callback endpoint raised `homeassistant.data_entry_flow.UnknownFlow`
- JWT decoded successfully (otherwise would see "Invalid state" from JWT validation)
- Flow created at `13:37:38` but callback tested at `15:52:55` (2+ hours later)
- Config flows have a timeout (typically 10-15 minutes)
- When flow expires, `UnknownFlow` gets translated to "Invalid state" error in browser

## Current Problem

We're building the COMPLETE URL including the state parameter:
```python
params = {
    "client_id": self.client_id,
    "scope": "profile:user_id",
    "response_type": "code",
    "redirect_uri": redirect_uri,
    "state": flow_id,  # ← We're adding state here
    "code_challenge": challenge,
    "code_challenge_method": "S256",
}
authorize_url = f"{self.authorize_url}?{urlencode(params)}"
return authorize_url
```

But the framework's `async_step_auth()` does something like:
```python
url = await implementation.async_generate_authorize_url(flow_id)
# Framework ADDS state parameter with JWT encoding here
final_url = f"{url}&state={_encode_jwt(...)}"
```

This creates **duplicate state parameters** or **incorrect JWT encoding**.

## Solution Options

### Option 1: Use Framework's LocalOAuth2Implementation (RECOMMENDED)
- Extend `LocalOAuth2Implementation` from framework
- Override `extra_authorize_data` property to add PKCE params
- Let framework handle everything else
- **Problem**: Requires checking if `LocalOAuth2ImplementationWithPkce` exists in HA 2024.10.0

### Option 2: Minimal Override (CURRENT APPROACH - FIX NEEDED)
- Keep our `AbstractOAuth2Implementation` extension
- **Don't include state in our URL** - return URL without state parameter
- Let framework add JWT-encoded state
- Override only `async_resolve_external_data` for PKCE verifier handling

## ROOT CAUSE CONFIRMED (2025-11-02 13:15 UTC)

**The Problem**: We are MANUALLY adding the state parameter to the authorization URL in our `async_generate_authorize_url()` method, but the framework's `AbstractOAuth2FlowHandler.async_generate_authorize_url()` method **ALSO adds the state parameter** via a different mechanism.

**Framework Flow**:
```python
# In AbstractOAuth2FlowHandler.async_generate_authorize_url():
url = await self.flow_impl.async_generate_authorize_url(self.flow_id)
# Framework does NOT add state here - it's handled elsewhere!
return str(URL(url).update_query(self.extra_authorize_data))
```

**Our Bug**: In `oauth.py` lines 180-191, we're adding state ourselves:
```python
params = {
    "client_id": self.client_id,
    "scope": "profile:user_id",
    "response_type": "code",
    "redirect_uri": redirect_uri,
    "state": _encode_jwt(...),  # ← WE ADD STATE HERE
    "code_challenge": challenge,
    "code_challenge_method": "S256",
}
```

**The Fix**: Remove state from our params. The framework adds it via `async_external_step()`.

## Next Steps

1. Remove state parameter from our `async_generate_authorize_url()` implementation
2. Remove scope from params (it's added via `extra_authorize_data` property)
3. Return URL with ONLY: client_id, response_type, redirect_uri, code_challenge, code_challenge_method
4. Let framework add state and scope via its own mechanisms
5. Test that authorization URL is correct

## Framework Version Check Needed

```bash
ssh root@haboxhill.local "python3 -c 'from homeassistant.helpers.config_entry_oauth2_flow import LocalOAuth2Implementation; print(\"EXISTS\")'"
```

If this succeeds, we can use LocalOAuth2Implementation. If it fails, we need Option 2.

## THE ACTUAL SOLUTION (2025-11-02 16:30 UTC)

**ROOT CAUSE**: Hardcoded redirect_uri bypassing Nabu Casa Cloud routing!

**The Problem**:
Our implementation was calculating redirect_uri in `__init__`:
```python
self.redirect_uri = f"{hass.config.external_url}/auth/external/callback"
```

This created the URL: `https://0gdzommh4w1tug2s97xnn9spylzmkkbs.ui.nabu.casa/auth/external/callback`

**But Home Assistant's OAuth framework expects**:
```python
@property
def redirect_uri(self) -> str:
    return async_get_redirect_uri(self.hass)
```

Which returns: `https://my.home-assistant.io/redirect/oauth` when "my" integration is active!

**What Happens with Nabu Casa Cloud**:
1. User clicks "Authorize" → Amazon OAuth site
2. Amazon redirects to: `https://0gdzommh4w1tug2s97xnn9spylzmkkbs.ui.nabu.casa/auth/external/callback?code=...&state=...`
3. Nabu Casa Cloud receives request but **doesn't recognize the state JWT** because it wasn't routed through my.home-assistant.io!
4. Nabu Casa returns "Invalid state" error WITHOUT forwarding to HA instance
5. No logs appear in HA because request never arrived!

**The Fix (2025-11-02 16:25 UTC)**:
```python
@property
def redirect_uri(self) -> str:
    """Return the redirect URI for OAuth callback.

    Uses framework's async_get_redirect_uri() which returns:
    - https://my.home-assistant.io/redirect/oauth if "my" integration is active (Nabu Casa Cloud routing)
    - Otherwise: frontend base URL + /auth/external/callback
    """
    return config_entry_oauth2_flow.async_get_redirect_uri(self.hass)
```

**Status**: ✅ OAuth implementation FIXED and WORKING! Integration successfully configured.

---

## ARCHITECTURE CLARIFICATION (2025-11-02 17:00 UTC)

**CRITICAL UNDERSTANDING**: This OAuth integration is designed to **replace** the legacy core Alexa integration.

### Legacy Core `alexa` Integration (Being Replaced)

**Configuration**: YAML-based (`configuration.yaml`)
```yaml
alexa:
  smart_home:
    client_id: "amzn1.application-oa2-client.xxx"
    client_secret: "secret"
    # Optional: endpoint filter, locale, etc.
```

**Security Model**:
- Exposes endpoint: `/api/alexa/smart_home` (unauthenticated or optional OAuth)
- **MAJOR FLAW**: Amazon requests NOT cryptographically validated!
- Relies on Nabu Casa Cloud OR Amazon's network security for request validation
- User quote: "The current core integration impersonates the MFA provider" (security vulnerability)

**Nabu Casa Dependency**: **NO** - Works without Nabu Casa Cloud
- Can expose `/api/alexa/smart_home` via HTTPS (self-hosted)
- No OAuth2 config flow - credentials in YAML only

**Use Case**: Legacy users with YAML configuration, accepts Amazon Smart Home requests

### Our New OAuth2 Integration (Replacement)

**Configuration**: UI-based Config Flow (OAuth2 + PKCE)
- User initiates via Settings → Integrations → Add Integration → Alexa
- Redirected to Amazon OAuth with PKCE for secure authentication
- Tokens stored encrypted, automatically refreshed

**Security Model**:
- OAuth2 with PKCE (RFC 7636) prevents code interception
- Tokens encrypted at rest (Fernet + PBKDF2)
- Cryptographically validates all Amazon requests
- **FIXES the security vulnerability** in legacy integration

**Nabu Casa Dependency for OAuth Flow**: **YES** (for OAuth callback routing)
- Uses `https://my.home-assistant.io/redirect/oauth` for OAuth callbacks
- **BUT**: After OAuth complete, Smart Home endpoint can work without Nabu Casa
- Framework's `async_get_redirect_uri()` handles routing automatically

**Nabu Casa Dependency for Smart Home Endpoint**: **TBD**
- Legacy integration works without Nabu Casa
- New integration should maintain this capability
- **DECISION NEEDED**: Should we support both Nabu Casa AND self-hosted HTTPS?

### Migration Strategy Implications

1. **Entity ID Preservation**: Critical - users have automations depending on entity IDs
2. **Side-by-Side Initially**: Allow both integrations during transition period
3. **YAML Migration**: Auto-import YAML config into new OAuth integration
4. **Deprecation Timeline**: 18-24 months with clear warnings
5. **Rollback Safety**: Preserve YAML config during migration for fallback

### Outstanding Questions

1. **Should new integration support BOTH**:
   - OAuth flow via Nabu Casa (`my.home-assistant.io/redirect/oauth`)
   - Smart Home endpoint via self-hosted HTTPS (like legacy)?

2. **How to handle users without Nabu Casa**:
   - Require Nabu Casa for initial OAuth setup?
   - Support direct HTTPS OAuth callback (requires registered domain)?
   - Document Nabu Casa as "recommended" vs "required"?

3. **Migration UX**:
   - Automatic YAML import on first load?
   - Manual migration button in UI?
   - Preserve both during transition?

---

## MIGRATION STRATEGY (2025-11-02 17:30 UTC)

**Document**: See `MIGRATION_STRATEGY.md` for comprehensive plan.

### Recommended Approach

**Phase 1 (Months 1-6): Side-by-Side**
- Deploy as `alexa_oauth2` custom component
- Both legacy and OAuth integrations coexist
- Users test new integration without removing YAML
- 1000+ beta users, gather feedback

**Phase 2 (Months 7-18): Migration Tool**
- Auto-import YAML configuration into OAuth flow
- Entity ID preservation (critical for automations!)
- Deprecation warning in UI: "Migrate to OAuth2 for enhanced security"
- 80%+ user migration target

**Phase 3 (Months 19-24): Core Replacement**
- Rename `alexa_oauth2` → `alexa` (domain replacement)
- Remove YAML support entirely
- Archive legacy code for rollback if needed
- 100% migration completion

### Key Technical Decisions

1. **OAuth Without Nabu Casa**: Support custom domain callbacks for self-hosted users
   - Framework's `async_get_redirect_uri()` handles both automatically
   - Maintain feature parity with legacy integration

2. **Entity ID Preservation**: Use entity registry transfer
   ```python
   entity_registry.async_update_entity(
       entity.entity_id,
       new_config_entry_id=new_entry.entry_id,
   )
   ```

3. **Rollback Safety**:
   - Auto-backup YAML config to `.storage/`
   - Preserve legacy integration as `custom_components/alexa_legacy/`
   - Clear documentation for reverting

### Success Metrics

- **Phase 1**: <1% rollback rate, 1000+ beta users
- **Phase 2**: 80%+ migration, entity IDs preserved
- **Phase 3**: 100% migration, zero critical bugs

### Timeline

- **Month 0**: Submit ADR to HA core team ← **NEXT STEP**
- **Month 1**: Beta release as custom component
- **Month 7**: Merge to HA core as `alexa_oauth2`
- **Month 12**: Enable deprecation warnings
- **Month 24**: Legacy integration removed

---

## IMMEDIATE NEXT STEPS

1. **Create ADR (Architecture Decision Record)**: Submit to HA core team for review
2. **Fork Core `alexa`**: Create working branch for OAuth2 implementation
3. **Test Coverage**: Expand to 95%+ including migration scenarios
4. **Documentation**: Create user-facing migration guide
5. **Beta Testing**: Recruit volunteer users for Phase 1 testing
