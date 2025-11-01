# Architectural Decisions & Rationale

**Purpose**: Document WHY we made certain choices
**Audience**: Future maintainers, decision reviewers
**Last Updated**: November 1, 2025

---

## Decision 1: OAuth2 with PKCE for Authentication

**Date**: October 30, 2025
**Status**: ACCEPTED & IMPLEMENTED
**Scope**: Entire authentication system

### The Question
How should users authenticate their Amazon account with Home Assistant?

### The Decision
**Use OAuth2 with PKCE (RFC 7636)** instead of Basic Auth, API Keys, or OAuth2 without PKCE

### Why This Decision?

**Security**: PKCE prevents authorization code interception attacks
- Without PKCE: Authorization code can be used by anyone who intercepts it
- With PKCE: Authorization code requires original verifier (proves device ownership)
- Home Assistant custom integrations are untrusted (could be modified), so PKCE is essential

**Compliance**: Amazon Alexa requires OAuth2 for account linking
- No alternative authentication method available
- Must use standard OAuth2 flow

**User Experience**: Single sign-on with Amazon
- Users authenticate with Amazon (familiar)
- Users don't share passwords with untrusted app
- One-time setup with automatic token renewal

**Industry Standard**: Designed for exactly this use case
- RFC 6749 (OAuth2) + RFC 7636 (PKCE) well-established
- Supported by all major cloud providers
- OWASP recommends PKCE for all OAuth2 flows

### Consequences

**Positive**:
- ✅ Secure by default (no secrets on device)
- ✅ User-friendly (familiar OAuth flow)
- ✅ Automatic token refresh (never expires from user perspective)
- ✅ Revocation possible (user disables access from Amazon account)

**Tradeoffs**:
- ❌ More complex than basic auth
- ❌ Extra network round-trips
- ❌ Requires internet for initial setup

### Verification
✅ Decided before implementation, fully implemented, production-ready

---

## Decision 2: Token Encryption at Rest (Fernet + PBKDF2)

**Date**: October 30, 2025
**Status**: ACCEPTED & IMPLEMENTED
**Scope**: Token storage

### The Question
How should we store OAuth2 tokens safely on disk?

### The Decision
**Encrypt tokens with Fernet (AES-128-CBC + HMAC)** using keys derived from PBKDF2 with 600,000 iterations

### Why This Decision?

**Threat Model**: Filesystem compromise (attacker has disk read access)
- Home Assistant storage directory could be compromised
- Without encryption: Attacker extracts tokens → gains Amazon access
- With encryption: Attacker only gets ciphertext, useless without key

**OWASP Compliance**:
- PBKDF2 with 600,000+ iterations for key derivation (OWASP standard)
- Fernet provides authenticated encryption (prevents tampering)
- Per-installation salt (prevents rainbow table attacks)

**No Secrets on Device**:
- PKCE eliminates need for client secret
- Tokens are only credentials stored
- Must be protected with encryption

**Deterministic Key**:
- Key derived from combination of system properties
- Same input always produces same key
- Different device produces different key
- Key discarded after use, never written to disk

### Consequences

**Positive**:
- ✅ Tokens safe even if filesystem compromised
- ✅ Encryption transparent to user
- ✅ Per-device keys (can't use token on different device)
- ✅ OWASP-compliant algorithm choices

**Tradeoffs**:
- ❌ Can't manually inspect token file (security > convenience)
- ❌ Small performance overhead (encryption/decryption)
- ❌ Key derivation is slow (600k iterations intentional for security)

### Verification
✅ All tokens encrypted before storage, all tokens decrypted on access, 100% test coverage

---

## Decision 3: Background Token Refresh Task

**Date**: October 31, 2025
**Status**: ACCEPTED & IMPLEMENTED
**Scope**: Session management

### The Question
How should tokens be refreshed before expiry?

### The Decision
**Implement background asyncio task** that:
- Runs every 60 seconds
- Checks token expiry
- Refreshes if expiry < 5 minutes away
- Runs continuously in background

### Why This Decision?

**User Experience**: Tokens never expire from user perspective
- Automatic refresh without user interaction
- Transparent operation
- No manual re-authentication needed

**Reliability**: Safety margin before expiry
- 5-minute buffer prevents expiry during API calls
- Token expires in 1 hour, refreshed at 55-minute mark
- Handles clock skew and network delays

**Concurrency Safety**: Single-flight pattern prevents duplicate requests
- Multiple components checking token concurrently
- Only one refresh happens (not N refreshes)
- Other components wait and reuse refreshed token

**Graceful Degradation**: Continue with stale token if refresh fails
- Temporary provider outage doesn't break system
- User sees degraded performance, not complete failure
- Exponential backoff prevents overwhelming provider

### Consequences

**Positive**:
- ✅ Users never see token expiry
- ✅ Automatic, no user action needed
- ✅ Graceful degradation on failures
- ✅ Minimal resource overhead (runs every 60 seconds)

**Tradeoffs**:
- ❌ Background task always running (consumes some CPU)
- ❌ More complex than on-demand refresh
- ❌ Potential for race conditions (mitigated with single-flight)

### Verification
✅ Background task working, token refresh tested, 47 session-related tests passing

---

## Decision 4: Advanced Reauth Handling (Multi-Scenario)

**Date**: October 31, 2025
**Status**: ACCEPTED & IMPLEMENTED
**Scope**: Error handling

### The Question
How should we handle token expiry and revocation scenarios?

### The Decision
**Implement advanced reauth detection** for 5 failure scenarios:

1. **Token Expired**: Normal expiry cycle (every 1-2 months)
2. **Token Revoked**: User disabled access in Amazon account
3. **Token Invalid**: Corrupted or tampered token
4. **Permission Lost**: Provider revoked access (rare)
5. **Refresh Token Expired**: Refresh token itself expired (60-90 days)

### Why This Decision?

**Reliability**: Different failures require different handling
- Expired token: Normal flow, just refresh
- Revoked token: Require user re-authentication
- Invalid token: Error state, ask for help
- Each scenario needs different user message

**User Communication**: Clear, actionable error messages
- User knows why re-authentication needed
- User knows what to do next
- Prevents confusion and support burden

**Automatic Recovery**: Attempt recovery before prompting user
- Retry with exponential backoff (5s, 10s, 20s)
- Only prompt user after 3 failed attempts
- Transparent operation for transient failures

### Consequences

**Positive**:
- ✅ Handles all realistic failure scenarios
- ✅ Clear user communication
- ✅ Automatic recovery for transient failures
- ✅ 100% test coverage on reauth paths

**Tradeoffs**:
- ❌ Complex failure detection logic
- ❌ More code paths to test
- ❌ Potential race conditions (mitigated with locks)

### Verification
✅ All 5 scenarios implemented and tested, reauth tests passing, production-ready

---

## Decision 5: Atomic YAML Migration with Rollback

**Date**: October 31, 2025
**Status**: ACCEPTED & IMPLEMENTED
**Scope**: Legacy system migration

### The Question
How should users migrate from legacy YAML-based integration?

### The Decision
**Implement atomic all-or-nothing migration** with:
- Backup before migration
- Rollback capability (within 2 minutes)
- Three-way reconciliation (verify data integrity)
- Device pairing preserved

### Why This Decision?

**User Trust**: Migration is all-or-nothing, no partial state
- Either all devices migrate or none do
- No orphaned configuration
- User confidence in migration process

**Data Safety**: Backup and rollback capability
- User can rollback within 2 minutes if issues found
- Original YAML preserved as backup
- Three-way reconciliation validates success

**Seamless Transition**: No manual re-pairing needed
- Devices discovered by existing pairing
- Alexa device list preserved
- User doesn't need to re-authorize Alexa

### Consequences

**Positive**:
- ✅ Zero data loss migration
- ✅ Rollback capability gives user confidence
- ✅ Devices preserved (no re-pairing)
- ✅ Atomic transaction prevents partial state

**Tradeoffs**:
- ❌ Complex migration code (820 lines)
- ❌ Backup overhead (extra storage)
- ❌ More failure points to handle

### Verification
✅ Migration code complete, all 57 migration tests passing, backup/restore tested

---

## Decision 6: HACS-First Deployment

**Date**: October 31, 2025
**Status**: ACCEPTED & IMPLEMENTED
**Scope**: Installation method

### The Question
What's the primary installation method for users?

### The Decision
**HACS (Home Assistant Community Store)** is primary installation method
- User adds custom repository
- User installs via HACS UI
- Automatic updates via HACS

### Why This Decision?

**User Experience**: Standard for Home Assistant integrations
- Users familiar with HACS workflow
- One-click installation
- Automatic update notifications

**Distribution**: No need for manual GitHub clone
- Avoids file permission issues
- Easier for non-technical users
- Standard practice in HA community

**Beta Testing**: Easy beta distribution
- Users opt-in via custom repository
- Easy rollback (previous versions available)
- Feedback collection point

### Consequences

**Positive**:
- ✅ Standard installation method
- ✅ Easy for users
- ✅ Good for beta testing
- ✅ Path to official HACS listing

**Tradeoffs**:
- ❌ Requires GitHub repository (public)
- ❌ Semantic versioning discipline needed
- ❌ Requires manifest.json maintenance

### Verification
✅ HACS packaging complete, manifest.json validated, hacs.json configured

---

## Decision 7: Clean Architecture Documentation

**Date**: November 1, 2025
**Status**: ACCEPTED & IMPLEMENTED
**Scope**: Documentation structure

### The Question
How should we organize documentation for long-term maintainability?

### The Decision
**Implement Clean Architecture principles** for documentation with 6 layers:

1. **Layer 00**: ARCHITECTURE (technology-agnostic principles)
2. **Layer 01**: USE_CASES (user workflows)
3. **Layer 02**: REFERENCE (quick lookups)
4. **Layer 03**: INTERFACES (API contracts)
5. **Layer 04**: INFRASTRUCTURE (implementation details)
6. **Layer 05**: OPERATIONS (procedures and troubleshooting)

### Why This Decision?

**Dependency Rule**: Inner layers never reference outer layers
- Layer 00 (abstract) independent of Layer 05 (concrete)
- Can change implementation without updating principles
- Documentation stable and refactorable

**Clear Responsibility**: Each layer has single responsibility
- Layer 00: WHY (principles)
- Layer 01: WHAT (workflows)
- Layer 02: QUICK LOOKUP (glossary)
- Layer 03: CONTRACTS (interfaces)
- Layer 04: HOW (implementation)
- Layer 05: PROCEDURES (operations)

**Session Resumption**: Easy to re-orient after time away
- 00_QUICKSTART.md (30 seconds)
- SESSION_RESUMPTION.md (2 minutes)
- Clear documentation structure for finding info

### Consequences

**Positive**:
- ✅ Easy to find documentation
- ✅ Clear organization
- ✅ Refactorable (change layer 04 without affecting 00)
- ✅ Long-term maintainability

**Tradeoffs**:
- ❌ More files (26 files total)
- ❌ Learning curve (understand layer structure)
- ❌ Some duplication risk (same concept in multiple layers)

### Verification
✅ 6-layer structure complete, entry points created, Dependency Rule verified

---

## Decision 8: Fix Redirect URI from /redirect/oauth to /redirect/alexa

**Date**: November 1, 2025
**Status**: ACCEPTED & IMPLEMENTED
**Scope**: OAuth2 callback

### The Question
What redirect URI should be used for OAuth2 callback?

### The Decision
**Use Amazon Alexa-specific path**: `https://my.home-assistant.io/redirect/alexa`
(Changed from generic: `https://my.home-assistant.io/redirect/oauth`)

### Why This Decision?

**Amazon Requirement**: Alexa Skills require Alexa-specific redirect path
- Generic /redirect/oauth path doesn't work
- Amazon expects /redirect/alexa for Alexa integrations
- Must match exactly (case-sensitive)

**Specificity**: Provider-specific path is better practice
- Different providers use different paths
- Explicit about which skill/integration is being configured
- Prevents path collisions

**Verification**: User provided evidence
- Nabu Casa screenshot showing URL functional
- FAQ confirmation that my.home-assistant.io supports custom paths
- Safe to use Alexa-specific path

### Consequences

**Positive**:
- ✅ Compatible with Amazon Alexa Skills
- ✅ More specific than generic path
- ✅ All 171 tests passing with new URI

**Tradeoffs**:
- ❌ Found after significant development (3 days)
- ❌ Required widespread code updates (10+ occurrences)
- ❌ User had to question assumption

### Lesson Learned
**Always verify architectural assumptions** before multi-day implementation. If unsure about redirect URL availability, verify with strategic consultants immediately.

### Verification
✅ Updated in 4 source files + 6 test files, all 171 tests passing with new URI

---

## Summary of Decisions

| # | Decision | Status | Impact |
|---|----------|--------|--------|
| 1 | OAuth2 with PKCE | ✅ IMPLEMENTED | Entire auth system |
| 2 | Token encryption (Fernet+PBKDF2) | ✅ IMPLEMENTED | Token storage |
| 3 | Background refresh task | ✅ IMPLEMENTED | Session management |
| 4 | Advanced reauth handling | ✅ IMPLEMENTED | Error handling |
| 5 | Atomic YAML migration | ✅ IMPLEMENTED | Migration system |
| 6 | HACS-first deployment | ✅ IMPLEMENTED | Installation method |
| 7 | Clean Architecture docs | ✅ IMPLEMENTED | Documentation |
| 8 | Alexa-specific redirect URI | ✅ IMPLEMENTED | OAuth2 callback |

---

## Dependency Between Decisions

```
Decision 1 (OAuth2+PKCE)
    ↓ requires
Decision 2 (Token encryption)
    ↓ requires
Decision 3 (Background refresh)
    ↓ requires
Decision 4 (Reauth handling)
    ↓ requires
Decision 8 (Correct redirect URI)
    ↓ enables
Decision 6 (HACS deployment)
    ↓ requires
Decision 5 (YAML migration)
    ↓ documented by
Decision 7 (Clean Architecture)
```

---

## Future Decisions (When Needed)

**Not Yet Decided**:
- Device discovery API integration (Phase 5)
- Smart home control (Phase 6)
- Multi-language support (Phase 7)
- Rate limiting strategy
- Metrics/telemetry collection
- Update delivery mechanism (semantic versioning refinement)

**When to Revisit**:
- After beta testing feedback
- When adding new features
- If security issues discovered
- When performance becomes concern

---

**Last Updated**: November 1, 2025
**Phase**: Beta Testing Preparation
**Next Decision**: User feedback on OAuth testing
