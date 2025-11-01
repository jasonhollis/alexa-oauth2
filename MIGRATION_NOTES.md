# Migration to Home Assistant OAuth2 Framework

**Date**: 2025-11-01
**Status**: Files rewritten, but CRITICAL ISSUE identified

## Summary

Rewrote `config_flow.py` and `__init__.py` to use Home Assistant's built-in OAuth2 framework with our custom PKCE implementation from `oauth.py`.

## Files Changed

### 1. `/custom_components/alexa/config_flow.py` (Complete Rewrite)

**Before**: 646 lines with manual OAuth handling
**After**: 223 lines using `AbstractOAuth2FlowHandler`

**Key Changes**:
- Inherits from `config_entry_oauth2_flow.AbstractOAuth2FlowHandler`
- Framework handles all OAuth mechanics (redirect, callback, state validation)
- Only need to implement:
  - `extra_authorize_data`: Provides OAuth scope
  - `async_oauth_create_entry`: Fetches user profile and creates entry
- Removed all manual OAuth code (URL generation, state management, token exchange)
- Removed reauth flow (framework handles this automatically)
- Added unique_id based on Amazon user_id (prevents duplicate accounts)

**New Flow**:
1. User initiates setup → `async_step_user()`
2. Framework redirects to Amazon OAuth (using our `AlexaOAuth2Implementation`)
3. Amazon redirects back → framework validates and exchanges code
4. Framework calls `async_oauth_create_entry()`
5. We fetch Amazon profile for unique_id
6. Create ConfigEntry with tokens

### 2. `/custom_components/alexa/__init__.py` (Complete Rewrite)

**Before**: 278 lines with custom session/token management
**After**: 359 lines using framework's OAuth2Session

**Key Changes**:
- Removed custom `SessionManager`, `TokenManager`, `OAuthManager` classes
- Uses framework's `OAuth2Session` for token management
- Framework handles token storage, refresh, and reauth triggers
- OAuth implementation registration moved from config_flow to here
- Stores `OAuth2Session` in hass.data for platforms to use

**New Flow**:
1. `async_setup_entry()` called after config flow creates entry
2. Get OAuth implementation from framework
3. Create `OAuth2Session` for this entry
4. Store session in hass.data
5. Forward to platforms (if any)

## CRITICAL ISSUE: Implementation Registration Chicken-and-Egg Problem

### The Problem

Home Assistant's OAuth2 framework has a **chicken-and-egg problem** with custom implementations:

1. **Framework Expectation**: OAuth implementations should be registered BEFORE config flow runs
2. **Our Requirement**: We need `client_id` and `client_secret` from the user to register our implementation
3. **Conflict**: We can't register the implementation until we have credentials, but the framework needs the implementation to run the OAuth flow

### Current State

The rewritten code has this issue documented in `__init__.py` lines 159-200:

```python
if DOMAIN not in current_implementations:
    _LOGGER.error(
        "OAuth implementation not registered. This should have been "
        "done during config flow. Please remove and re-add the integration."
    )
    return False
```

This will **FAIL** on first setup because the implementation isn't registered yet.

### Home Assistant's Official Solution

Use the **Application Credentials** integration (introduced in 2024.x):

1. User adds application credentials via Settings → Integrations → Application Credentials
2. Credentials stored globally (not per-entry)
3. OAuth implementations registered globally using those credentials
4. Config flow can then use pre-registered implementations

**Reference**: https://www.home-assistant.io/integrations/application_credentials/

### Options to Fix

#### Option 1: Use Application Credentials (Recommended for HA Core)

**Pros**:
- Standard Home Assistant pattern
- Supports multiple accounts cleanly
- Credentials managed separately from entries

**Cons**:
- Requires additional setup step (user must add credentials first)
- More complex UX (two-step setup)
- Requires creating `application_credentials.py`

**Implementation**:
1. Create `application_credentials.py` with `async_get_auth_implementation()`
2. Register as application_credentials platform in manifest.json
3. User adds credentials via UI → implementation auto-registered
4. Config flow uses pre-registered implementation

#### Option 2: Register Implementation in Config Flow (Hack)

**Pros**:
- Single-step setup (user provides credentials in one flow)
- Simpler UX

**Cons**:
- Not the "official" Home Assistant pattern
- Implementation registered during config flow (non-standard)
- May break with future HA versions

**Implementation**:
1. In `config_flow.py`, before calling `super().async_step_user()`:
   ```python
   # Register implementation if not exists
   if DOMAIN not in config_entry_oauth2_flow.async_get_implementations(self.hass, DOMAIN):
       config_entry_oauth2_flow.async_register_implementation(
           self.hass,
           DOMAIN,
           AlexaOAuth2Implementation(
               self.hass,
               DOMAIN,
               user_input[CONF_CLIENT_ID],
               user_input[CONF_CLIENT_SECRET],
           ),
       )
   ```

2. In `__init__.py`, remove the error check (implementation will already be registered)

#### Option 3: Hybrid Approach

Support BOTH methods:
- If application credentials exist → use them (Option 1)
- If not → allow inline credentials (Option 2)

This provides flexibility for custom integrations vs. HA Core inclusion.

### Recommendation

**For Custom Integration (HACS)**: Use **Option 2** (register in config flow)
- Simpler user experience
- Single-step setup
- Works fine for custom integrations

**For HA Core Inclusion**: Use **Option 1** (application credentials)
- Follows official Home Assistant patterns
- Required for core integrations
- More maintainable long-term

## Next Steps

### Immediate Fix (Option 2)

1. **Update `config_flow.py`**:
   - Add step to collect client_id/client_secret BEFORE OAuth
   - Register `AlexaOAuth2Implementation` after credentials collected
   - Then proceed with OAuth flow

2. **Update `__init__.py`**:
   - Remove error check for missing implementation
   - Implementation will already be registered by config flow

### Files to Modify

1. `config_flow.py`:
   - Add `async_step_credentials()` to collect client_id/client_secret
   - Register implementation before OAuth redirect
   - Keep rest of OAuth flow as-is

2. `__init__.py`:
   - Remove lines 162-200 (error check)
   - Implementation will already exist from config flow

### Code Example for config_flow.py Fix

```python
async def async_step_user(
    self, user_input: dict[str, Any] | None = None
) -> FlowResult:
    """Handle a flow initialized by the user."""
    # First, collect credentials
    return await self.async_step_credentials()

async def async_step_credentials(
    self, user_input: dict[str, Any] | None = None
) -> FlowResult:
    """Collect client_id and client_secret."""
    if user_input is None:
        return self.async_show_form(
            step_id="credentials",
            data_schema=vol.Schema({
                vol.Required(CONF_CLIENT_ID): str,
                vol.Required(CONF_CLIENT_SECRET): str,
            }),
        )

    # Register OAuth implementation with user's credentials
    config_entry_oauth2_flow.async_register_implementation(
        self.hass,
        DOMAIN,
        AlexaOAuth2Implementation(
            self.hass,
            DOMAIN,
            user_input[CONF_CLIENT_ID],
            user_input[CONF_CLIENT_SECRET],
        ),
    )

    # Now proceed with OAuth flow
    return await super().async_step_user(user_input)
```

## Testing Checklist

After implementing the fix:

- [ ] Fresh install works (new integration)
- [ ] Multiple accounts can be added (different Amazon user_ids)
- [ ] Same account cannot be added twice (unique_id check)
- [ ] Reauth flow works (when tokens expire)
- [ ] Token refresh works automatically (framework handles this)
- [ ] Integration unload works cleanly
- [ ] Removing integration cleans up properly

## Files Now Obsolete

After migration to framework OAuth2, these custom files are NO LONGER NEEDED:

- `oauth_manager.py` - Replaced by `AlexaOAuth2Implementation` + framework
- `token_manager.py` - Replaced by framework's token storage
- `session_manager.py` - Replaced by framework's `OAuth2Session`
- `exceptions.py` - Framework uses standard exceptions

These can be removed once the integration is tested and working.

## Benefits of Migration

1. **Less Code**: 869 lines → 582 lines (33% reduction)
2. **More Robust**: Framework-tested token refresh and storage
3. **Better UX**: Automatic reauth notifications
4. **Encrypted Storage**: Framework provides encrypted token storage
5. **Standard Patterns**: Follows Home Assistant best practices
6. **PKCE Support**: Maintained via our custom `AlexaOAuth2Implementation`

## Risks

1. **Breaking Change**: Existing installations will need to re-authenticate
2. **Migration Path**: Need to handle existing token storage → framework storage
3. **Testing**: Extensive testing required to ensure OAuth flow works correctly

## References

- Home Assistant OAuth2 Framework: https://developers.home-assistant.io/docs/config_entries_oauth2_flow
- Application Credentials: https://www.home-assistant.io/integrations/application_credentials/
- PKCE RFC 7636: https://datatracker.ietf.org/doc/html/rfc7636
- Amazon LWA Docs: https://developer.amazon.com/docs/login-with-amazon/
