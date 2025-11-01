# ADR-001: Why OAuth2 with PKCE for Authentication?

**Layer**: 00_ARCHITECTURE
**Type**: Architectural Decision Record
**Status**: ACCEPTED
**Decided**: November 1, 2025

---

## Context

**Question**: How should users authenticate their Amazon account with Home Assistant?

**Options Considered**:
1. Basic Authentication (username + password stored)
2. API Keys (manual token generation)
3. OAuth2 without PKCE (client secret on device)
4. OAuth2 with PKCE (RFC 7636) ← **CHOSEN**

**Constraints**:
- Amazon Alexa requires OAuth2 for account linking (no other option)
- Home Assistant integrations are untrusted code (could be modified by attackers)
- Users should not need to generate tokens manually
- Tokens must be refreshable (user shouldn't re-authenticate every 60 days)

---

## Decision

**Use OAuth2 with PKCE (RFC 7636) for authentication.**

### Why OAuth2?

✅ **Industry Standard**
- Supported by all major cloud providers (Amazon, Google, Microsoft)
- Designed specifically for delegated access
- RFC 6749 and RFC 7636 well-established

✅ **Security-First Design**
- Client never sees user credentials (user types password into Amazon, not HA)
- Credentials not stored in HA (reduces attack surface)
- Tokens are short-lived and automatically refreshed
- Token revocation possible without password change

✅ **User-Friendly**
- Users authenticate with Amazon (familiar flow)
- No password sharing with untrusted apps
- Single sign-on possible (if user already logged in to Amazon)
- One-time setup with automatic renewal

✅ **Amazon's Requirement**
- Amazon Alexa skill account linking requires OAuth2
- No alternative available
- Must use standard OAuth2 flow

### Why PKCE (RFC 7636)?

**PKCE = Proof Key for Code Exchange**

✅ **Designed for Untrusted Clients**
- Assumes client application cannot safely store secrets
- Home Assistant custom integrations are untrusted (could be modified)
- PKCE proves client possession without storing secrets on device

✅ **Prevents Authorization Code Interception**
- Classic OAuth2 vulnerability: Authorization code can be used by anyone
- PKCE solution: Authorization code useless without PKCE verifier
- Verifier proves "I'm the same system that requested this code"

✅ **No Client Secret on Device**
- Without PKCE: Client secret must be stored, can be extracted
- With PKCE: No secret needed, just random verifier
- Eliminated attack vector: Device compromise doesn't leak client secret

✅ **Recommended by OWASP**
- OWASP recommends PKCE for all OAuth2 flows (not just mobile)
- Amazon's own LWA documentation recommends PKCE
- Industry best practice, not just security theater

---

## Consequences

### Positive

✅ **Secure by Default**
- No credentials stored on device
- Tokens automatically refreshed
- User can revoke access from Amazon anytime
- Even if HA integration compromised, attacker can't forge tokens

✅ **User-Friendly**
- One-time setup (5 minutes)
- Automatic token refresh (never expires from user perspective)
- Clear authorization flow (user always in control)
- Familiar with other cloud integrations (Gmail, Dropbox, etc.)

✅ **Maintainable**
- Standard OAuth2 reduces custom security code
- PKCE reduces token storage complexity
- Automatic refresh reduces user support burden

### Tradeoffs

❌ **More Complex Than Basic Auth**
- OAuth2 flow more code than storing username/password
- PKCE requires cryptographic operations (code challenge)
- User experience: Extra redirect to Amazon login page

❌ **Network Dependent**
- Requires internet connection for token exchange
- Token refresh requires Amazon API reachability
- Initial setup requires Amazon authentication

❌ **Token Management Overhead**
- Must implement token refresh logic
- Must handle token expiry gracefully
- Must encrypt tokens at rest (can't store plaintext)

---

## Alternatives Rejected

### Alternative 1: Basic Authentication ❌

**What It Is**: User stores Amazon username + password in Home Assistant

**Why Not**:
- ❌ Credentials stored plaintext on device (major security risk)
- ❌ User must trust HA not to send credentials to Amazon (suspicious)
- ❌ No way to revoke without changing password
- ❌ If HA integration compromised, attacker has credentials
- ❌ Amazon Alexa account linking doesn't support Basic Auth
- ❌ OWASP explicitly recommends against for modern systems

**Example Weakness**:
- User stores: `{"username": "user@amazon.com", "password": "secret123"}`
- HA integration compromised by malicious actor
- Attacker extracts credentials from storage → Gains full Amazon account access
- User must change password everywhere Amazon is used

### Alternative 2: API Keys ❌

**What It Is**: User manually generates token in Amazon console, pastes into HA

**Why Not**:
- ❌ User must manually generate token (poor UX)
- ❌ User must manually rotate token every 60 days (HA won't auto-rotate)
- ❌ User must manually paste long strings (error-prone)
- ❌ Each HA instance needs separate token (multiple copies to manage)
- ❌ No refresh mechanism (user re-auth required every 60 days)
- ❌ Amazon doesn't provide simple API key option for Alexa

**Example Weakness**:
- User generates 40-character random token
- User must paste into HA UI (typos likely)
- Token expires in 60 days silently
- User notices HA stopped working (bad experience)
- User must go back to Amazon console, generate new token, paste again

### Alternative 3: OAuth2 Without PKCE ❌

**What It Is**: OAuth2 with client secret stored on device

**Why Not**:
- ❌ Client secret stored on device (not secure for untrusted software)
- ❌ Modified HA integration can extract client secret
- ❌ Client secret compromise requires new credential generation
- ❌ OWASP explicitly recommends PKCE for all flows
- ❌ Adds complexity without better security than PKCE

**Example Weakness**:
- HA stores: `{"client_id": "...", "client_secret": "secret12345"}`
- Malicious integration reads storage directory
- Attacker extracts client_secret
- Attacker can now exchange authorization code for tokens (PKCE absent)
- Amazon's LWA security violated

---

## Implementation Notes

### PKCE Flow in This System

1. **Generate Verifier** (256 bits entropy)
   - User initiates setup in HA UI
   - System generates random verifier: `e9mVObwhc_wr8N2...` (128 chars)

2. **Create Challenge** (SHA256 hash of verifier)
   - Challenge = SHA256(verifier)
   - Challenge used in step 3, verifier stored in memory only

3. **Request Authorization** (redirect user to Amazon)
   - HA sends to Amazon: client_id, redirect_url, **code_challenge**
   - Amazon shows: "Allow HA to access your Alexa account?"
   - User clicks: "Allow"

4. **Get Authorization Code** (Amazon redirects back)
   - Amazon redirects to HA with: authorization_code
   - HA extracts code from redirect URL

5. **Exchange Code + Verifier** (prove possession of challenge)
   - HA sends to Amazon: code, **verifier**, client_id
   - Amazon checks: SHA256(verifier) == code_challenge from step 3?
   - Yes → Amazon issues access token + refresh token
   - No → Authorization fails (prevents interception attacks)

### Token Encryption (Separate Layer)

Once tokens obtained:
- Tokens encrypted with AEAD (Fernet)
- Stored in Home Assistant storage
- Decrypted only when needed
- Encrypted again after use
- Encryption independent of PKCE layer

---

## Verification

### Is This Decision Technology-Agnostic?

**Test**: Can we implement this in completely different stack (Go, Rust, Node.js)?

✅ YES
- OAuth2 is language-agnostic protocol
- PKCE is standard (RFC 7636)
- Only HTTP calls needed
- Crypto libraries available in all languages

### Can We Change Implementation Without Changing Decision?

**Test**: Can we swap providers or token storage?

✅ YES (through interfaces)
- Can support Google OAuth2 instead of Amazon
- Can use database storage instead of local files
- Implementation details don't affect decision

### Does This Address the Original Constraint?

**Test**: Does this satisfy "Amazon Alexa requires OAuth2"?

✅ YES
- OAuth2 ✅
- PKCE ✅
- No client secret on device ✅
- Secure token refresh ✅

---

## Related Decisions

- None yet (this is the foundational authentication decision)

## Related Documents

- [Security Principles](SECURITY_PRINCIPLES.md) - How OAuth2 + PKCE provides security
- [System Overview](SYSTEM_OVERVIEW.md) - Architecture implementing this decision

---

**Decision Made**: November 1, 2025
**Implementation Status**: Complete (171 tests passing)
**Layer**: 00_ARCHITECTURE (This is an ADR, affects entire system)
