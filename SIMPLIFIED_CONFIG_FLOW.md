# Simplified Config Flow: Remove User Credential Input

**Problem**: Current implementation requires each user to create Amazon Developer account and provide client_id/client_secret.

**Solution**: Hardcode shared OAuth credentials for all Home Assistant users.

---

## Changes Required

### 1. Create Shared OAuth Application (One-Time Setup)

**Who**: Home Assistant Foundation / Nabu Casa team
**What**: Create Amazon LWA Security Profile with:
- Client ID: `amzn1.application-oa2-client.XXXXXXXXXX`
- Client Secret: `XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX`
- Allowed Return URLs:
  - `https://my.home-assistant.io/redirect/oauth` (Nabu Casa Cloud routing)
  - Additional URLs for self-hosted setups (if supported)

**Where to create**:
1. Go to https://developer.amazon.com/loginwithamazon/console/site/lwa/overview.html
2. Create Security Profile: "Home Assistant Alexa Integration"
3. Add return URLs
4. Copy client_id and client_secret

---

### 2. Hardcode Credentials in Integration

**File**: `custom_components/alexa/const.py`

```python
"""Constants for the Alexa integration."""

from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET

# Domain
DOMAIN = "alexa"

# OAuth2 Endpoints (Amazon LWA)
AMAZON_AUTH_URL = "https://www.amazon.com/ap/oa"
AMAZON_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
AMAZON_REVOKE_URL = "https://api.amazon.com/auth/o2/revoke"

# OAuth2 Scopes
REQUIRED_SCOPES = "profile:user_id"

# ⚠️ NEW: Shared OAuth Application Credentials
# These credentials are owned by Home Assistant Foundation and shared
# across all Home Assistant instances for Alexa integration.
#
# Security Note: Client secret is NOT sensitive for OAuth2 public clients
# using PKCE. PKCE prevents authorization code interception even if the
# client secret is known. See RFC 7636 Section 1.1.
#
# Alternative: Use application_credentials integration to allow users
# to optionally provide their own credentials (advanced use case).
OAUTH_CLIENT_ID = "amzn1.application-oa2-client.XXXXXXXXXXXXX"  # TODO: Replace with real client_id
OAUTH_CLIENT_SECRET = "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"  # TODO: Replace with real client_secret

# ... rest of constants ...
```

---

### 3. Simplify Config Flow (Remove Credential Input)

**File**: `custom_components/alexa/config_flow.py`

**REMOVE** the credential input form (lines 98-177):

```python
async def async_step_user(
    self, user_input: dict[str, Any] | None = None
) -> FlowResult:
    """Handle a flow initialized by the user.

    Simplified flow: No credential input needed. Uses hardcoded OAuth app.

    Flow:
        1. User clicks "Add Integration" → "Alexa"
        2. Immediately proceed to OAuth authorization
        3. User authorizes on Amazon's site
        4. Redirect back with code
        5. Exchange code for tokens
        6. Create ConfigEntry
    """
    # Check if implementation already registered
    current_implementations = await config_entry_oauth2_flow.async_get_implementations(
        self.hass, DOMAIN
    )

    if DOMAIN not in current_implementations:
        # Register OAuth implementation with hardcoded credentials
        from .const import OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET

        _LOGGER.debug("Registering AlexaOAuth2Implementation with shared credentials")
        config_entry_oauth2_flow.async_register_implementation(
            self.hass,
            DOMAIN,
            AlexaOAuth2Implementation(
                self.hass,
                DOMAIN,
                OAUTH_CLIENT_ID,
                OAUTH_CLIENT_SECRET,
            ),
        )

    # Get the implementation and set it as flow_impl
    implementations = await config_entry_oauth2_flow.async_get_implementations(
        self.hass, DOMAIN
    )
    self.flow_impl = implementations[DOMAIN]

    # Proceed directly to OAuth authorization
    return await self.async_step_auth()
```

**UPDATE** `__init__.py` to register implementation on startup:

```python
async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Amazon Alexa component."""
    hass.data.setdefault(DOMAIN, {})

    # Register shared OAuth implementation on startup
    from .const import OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET
    from .oauth import AlexaOAuth2Implementation

    current_implementations = await config_entry_oauth2_flow.async_get_implementations(
        hass, DOMAIN
    )

    if DOMAIN not in current_implementations:
        _LOGGER.debug("Registering AlexaOAuth2Implementation on startup")
        config_entry_oauth2_flow.async_register_implementation(
            hass,
            DOMAIN,
            AlexaOAuth2Implementation(
                hass,
                DOMAIN,
                OAUTH_CLIENT_ID,
                OAUTH_CLIENT_SECRET,
            ),
        )

    return True
```

---

### 4. Update Entry Setup (Remove Credential Extraction)

**File**: `custom_components/alexa/__init__.py`

**REMOVE** credential extraction from config entry (lines 165-176):

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Amazon Alexa from a config entry."""

    # Initialize integration storage if not exists
    hass.data.setdefault(DOMAIN, {})

    # Implementation already registered in async_setup()
    # Get implementation for this entry
    try:
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    except ValueError as err:
        _LOGGER.error("Failed to get OAuth implementation: %s", err)
        return False

    # Create OAuth2 session for this entry
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    # Test token validity
    try:
        await session.async_ensure_token_valid()
    except Exception as err:
        _LOGGER.error("Failed to validate OAuth token: %s", err)
        # Don't fail setup - framework will trigger reauth if needed

    # Store OAuth session
    hass.data[DOMAIN][entry.entry_id] = {
        "session": session,
        "implementation": implementation,
        "user_id": entry.data.get("user_id"),
        "name": entry.data.get("name"),
        "email": entry.data.get("email"),
    }

    # Forward to platforms (if any)
    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True
```

---

### 5. Security Considerations

**Q**: Isn't hardcoding the client_secret insecure?

**A**: NO - for OAuth2 public clients using PKCE:
- PKCE prevents authorization code interception even if client_secret is known (RFC 7636)
- Client secret is NOT meant to be secret for public clients (mobile apps, SPAs, etc.)
- Many OAuth providers (Google, GitHub) publicly document their client IDs/secrets

**Q**: Can someone abuse our OAuth application?

**A**: Limited risk:
- Rate limits per client_id at Amazon's end
- Each user must authorize in their own Amazon account
- PKCE prevents MITM attacks
- Home Assistant's state parameter prevents CSRF

**Q**: What if we want to allow advanced users to provide their own credentials?

**A**: Use Home Assistant's `application_credentials` integration:

```python
from homeassistant.helpers import config_entry_oauth2_flow

# Check for user-provided credentials first
app_creds = await application_credentials.async_get_application_credentials(
    hass, DOMAIN
)

if app_creds:
    # User provided their own OAuth app - use those credentials
    client_id = app_creds.client_id
    client_secret = app_creds.client_secret
else:
    # Use shared OAuth application
    from .const import OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET
    client_id = OAUTH_CLIENT_ID
    client_secret = OAUTH_CLIENT_SECRET
```

---

### 6. User Experience Comparison

**Current (Complex)**:
```
User → Settings → Integrations → Add Integration → Alexa
     → Form: "Enter Client ID"
     → Form: "Enter Client Secret"
     → Link: "How to get credentials?"
     → User goes to Amazon Developer Console
     → User creates account (if needed)
     → User creates Security Profile
     → User configures redirect URLs
     → User copies client_id and client_secret
     → User pastes into Home Assistant
     → OAuth flow begins
     → User authorizes
     → Done
```

**Proposed (Simple)**:
```
User → Settings → Integrations → Add Integration → Alexa
     → OAuth flow begins immediately
     → User authorizes on Amazon
     → Done
```

**Lines of code removed**: ~80 lines (credential input form)
**Setup time**: 30 minutes → 30 seconds
**User friction**: Eliminated

---

### 7. Implementation Checklist

- [ ] Home Assistant Foundation creates Amazon LWA Security Profile
- [ ] Add return URL: `https://my.home-assistant.io/redirect/oauth`
- [ ] Copy client_id and client_secret
- [ ] Add credentials to `const.py` as `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET`
- [ ] Remove credential input form from `config_flow.py`
- [ ] Register implementation in `async_setup()` instead of config flow
- [ ] Remove credential extraction from `async_setup_entry()`
- [ ] Test OAuth flow with hardcoded credentials
- [ ] Update documentation (remove "How to get credentials" instructions)
- [ ] Optional: Add `application_credentials` support for advanced users

---

### 8. Migration Path for Existing Users

**Problem**: Existing users have entries with their own client_id/client_secret.

**Solution**: Transparent migration in `async_setup_entry()`:

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Amazon Alexa from a config entry."""

    # Check if this is an old entry with user-provided credentials
    has_custom_creds = (
        "client_id" in entry.data and
        entry.data["client_id"] != OAUTH_CLIENT_ID
    )

    if has_custom_creds:
        _LOGGER.info(
            "Entry %s uses custom OAuth credentials (advanced setup). "
            "To use shared Home Assistant OAuth app, delete and re-add integration.",
            entry.entry_id
        )

        # Register custom implementation for this specific entry
        custom_impl = AlexaOAuth2Implementation(
            hass,
            f"{DOMAIN}_{entry.entry_id}",  # Unique domain for this entry
            entry.data["client_id"],
            entry.data["client_secret"],
        )
        config_entry_oauth2_flow.async_register_implementation(
            hass,
            f"{DOMAIN}_{entry.entry_id}",
            custom_impl,
        )
        implementation = custom_impl
    else:
        # Use shared implementation
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )

    # Rest of setup...
```

---

### 9. Alternative: application_credentials Integration

Home Assistant provides the `application_credentials` integration for managing OAuth credentials:

**File**: `custom_components/alexa/application_credentials.py`

```python
"""Application credentials platform for Alexa."""
from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant

from .const import AMAZON_AUTH_URL, AMAZON_TOKEN_URL, OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return authorization server for Alexa."""
    return AuthorizationServer(
        authorize_url=AMAZON_AUTH_URL,
        token_url=AMAZON_TOKEN_URL,
    )


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Return description placeholders for the credentials dialog."""
    return {
        "more_info_url": "https://developer.amazon.com/loginwithamazon/console/site/lwa/overview.html",
        "oauth_consent_url": AMAZON_AUTH_URL,
    }
```

**File**: `custom_components/alexa/manifest.json`

```json
{
  "domain": "alexa",
  "name": "Amazon Alexa",
  "codeowners": ["@home-assistant/core"],
  "config_flow": true,
  "dependencies": ["application_credentials"],
  "documentation": "https://www.home-assistant.io/integrations/alexa",
  "iot_class": "cloud_push",
  "requirements": [],
  "version": "2.0.0"
}
```

This allows:
1. **Default**: Use hardcoded shared OAuth app (simple for 99% of users)
2. **Advanced**: Users can add their own credentials via Settings → Application Credentials

---

## Summary

**Current Problem**: Integration requires developer setup (Amazon Developer account, Security Profile, etc.)

**Solution**: Hardcode shared OAuth application credentials

**Impact**:
- Setup time: 30 minutes → 30 seconds
- User friction: Eliminated
- Code complexity: Reduced (~80 lines removed)
- Accessibility: Anyone with Alexa account can use it

**Next Steps**:
1. Home Assistant Foundation creates Amazon LWA Security Profile
2. Implement changes above
3. Test end-to-end flow
4. Document for HA core submission
