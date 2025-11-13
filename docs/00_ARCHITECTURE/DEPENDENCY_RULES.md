# Component Dependency Rules

**Layer**: 00_ARCHITECTURE (Innermost - Technology Agnostic)
**Purpose**: Define stable dependency contracts between components
**Audience**: Architects, decision makers
**Stability**: Highest

---

## The Dependency Rule

**Core Principle**: Dependencies point inward, toward more abstract policies.

```
┌─────────────────────────────────────────┐
│  Concrete Implementation Details         │ (Most volatile)
│  (Database, files, HTTP libraries)       │
└─────────────────────────────────────────┘
         ↑
         | depends on
         |
┌─────────────────────────────────────────┐
│  Technology Integration Layer            │
│  (Device access, storage backends)       │
└─────────────────────────────────────────┘
         ↑
         | depends on
         |
┌─────────────────────────────────────────┐
│  Interfaces & Contracts                 │
│  (Token storage contract, OAuth API)    │
└─────────────────────────────────────────┘
         ↑
         | depends on
         |
┌─────────────────────────────────────────┐
│  High-Level Policies                    │ (Most stable)
│  (Token refresh logic, auth flow)       │
└─────────────────────────────────────────┘
```

**Rule**: Outer layers depend on inner layers, NEVER the reverse.

**Violation**: If inner layer references outer layer, coupling becomes bidirectional and system becomes fragile.

---

## Component Dependencies

### Allowed Dependencies (Correct Direction)

✅ **Refresh Logic depends on Token Storage Interface**
- Refresh logic (inner): "I need to save new tokens"
- Token storage (outer): "I provide a save method"
- Dependency: Refresh → Storage Interface
- Direction: Correct (inner depends on interface, not concrete storage)

✅ **Config Flow depends on OAuth Manager Interface**
- Config flow (inner): "I need to perform OAuth"
- OAuth manager (outer): "I provide OAuth exchange"
- Dependency: Flow → OAuth Interface
- Direction: Correct (UI depends on abstraction, not implementation)

✅ **Session Management depends on Token Interface**
- Session (inner): "I need current token"
- Token manager (outer): "I provide current token"
- Dependency: Session → Token Interface
- Direction: Correct (scheduler depends on interface, not storage)

### Forbidden Dependencies (Incorrect Direction)

❌ **Storage Implementation depends on Auth Policy**
- Would mean: "How I store tokens depends on what auth flow I'm implementing"
- Result: Changing auth strategy requires changing storage mechanism
- Violation: Storage is concrete (outer), auth policy is abstract (inner)

❌ **Concrete HTTP Client depends on OAuth Manager**
- Would mean: "HTTP library needs to know about OAuth Manager"
- Result: Can't swap HTTP library without changing OAuth logic
- Violation: HTTP client is concrete, OAuth manager is abstract

❌ **Database schema depends on Session Logic**
- Would mean: "Database design depends on refresh interval"
- Result: Changing refresh interval requires database migration
- Violation: Database is concrete, refresh logic is abstract

---

## Interface Contracts

### Token Storage Interface

```
Required methods:
- save_token(access_token, refresh_token, expires_at) → void
- get_token() → (access_token, expiry_time)
- revoke_token() → void
- has_valid_token() → bool

Implementations:
- Encrypted local file storage ✅
- Database storage ✅
- Cloud-backed storage ✅
- In-memory cache ✅

Invariant: Caller never knows which implementation is used
```

**Guarantee**: Refresh logic can work with any token storage that implements this interface.

### OAuth2 Manager Interface

```
Required methods:
- get_authorization_url(callback_url) → (url, state, verifier)
- exchange_code_for_token(code, verifier) → (access_token, refresh_token, expires_at)
- refresh_tokens(refresh_token) → (access_token, expires_at)
- revoke_tokens(refresh_token) → void

Implementations:
- Amazon LWA (Login with Amazon) ✅
- Google OAuth2 ✅
- Microsoft OAuth2 ✅
- Generic OIDC provider ✅

Invariant: Session logic never knows which OAuth provider is used
```

**Guarantee**: Session management works with any OAuth2 provider implementing this interface.

### Reauth Detection Interface

```
Required methods:
- should_trigger_reauth(error) → bool
- get_reauth_reason(error) → string

Implementations:
- Token expired scenario ✅
- Token revoked scenario ✅
- Token invalid scenario ✅
- Permission lost scenario ✅

Invariant: Caller handles reauth generically regardless of failure reason
```

**Guarantee**: Reauth logic works with any provider, doesn't need provider-specific error handling.

---

## Dependency Graph

### Correct (Acyclic) Dependency Graph

```
                    Config UI
                       ↓
              depends on
                       ↓
                OAuth Manager Interface
                       ↑
                  implemented by
                       |
         ┌─────────────┼─────────────┐
         ↓             ↓             ↓
    Amazon LWA    Google OAuth   Generic OIDC
    (concrete)    (concrete)     (concrete)


          Session Manager
                ↓
         depends on
                ↓
      Token Manager Interface
                ↑
            implemented by
                ↓
     Encrypted File Storage
        (concrete)


           Reauth Handler
                ↓
         depends on
                ↓
     Reauth Detection Interface
                ↑
            implemented by
                ↓
      Provider Error Parser
         (concrete)
```

**Key Property**: No cycles. Every dependency path eventually reaches a leaf (concrete implementation).

### Invalid (Cyclic) Dependency Graph - FORBIDDEN

```
❌ Config UI → OAuth Manager
❌ OAuth Manager → Token Storage
❌ Token Storage → Config UI
❌ (Cycle detected - fragile system)
```

---

## Dependency Inversion in Practice

### Example 1: Swapping Token Storage

**Before**: Refresh logic hardcoded to file storage
```
Refresh Logic
    ↓
    | depends directly on
    ↓
FileStorage.write(token)
```

**Problem**: To use database storage, must modify refresh logic code.

**After**: Refresh logic depends on interface
```
Refresh Logic
    ↓
    | depends on
    ↓
TokenStorageInterface
    ↑
    | implemented by
    ├─ FileStorage
    └─ DatabaseStorage
```

**Benefit**: Swap storage implementation without touching refresh logic. Can use FileStorage in tests, DatabaseStorage in production.

### Example 2: Swapping OAuth Provider

**Before**: Hard-coded to Amazon LWA
```
Config Flow
    ↓
    | depends directly on
    ↓
AmazonLWA.get_auth_url()
```

**Problem**: To support Google OAuth, must modify config flow.

**After**: Config flow depends on interface
```
Config Flow
    ↓
    | depends on
    ↓
OAuth2ManagerInterface
    ↑
    | implemented by
    ├─ AmazonLWA
    ├─ GoogleOAuth2
    └─ GenericOIDC
```

**Benefit**: Add new OAuth providers without touching config flow. User chooses provider at setup time.

---

## Coupling and Cohesion

### High Cohesion (Good)

Components with related responsibilities grouped together:

✅ **Token Management Component**
- Responsibility: Token encryption, storage, retrieval
- Reason to change: Only if token format changes or storage location changes
- Cohesion: Highly cohesive (all about token management)

✅ **OAuth Manager Component**
- Responsibility: OAuth2 flow orchestration
- Reason to change: Only if OAuth2 protocol or provider API changes
- Cohesion: Highly cohesive (all about OAuth2 protocol)

### Low Coupling (Good)

Components with minimal dependencies:

✅ **Session Manager ← Token Manager**
- Session knows about: TokenManager interface only
- Session doesn't know about: File storage, encryption algorithm, key derivation
- Coupling: Low (depends on interface, not implementation)

✅ **Reauth Handler ← OAuth Manager**
- Reauth knows about: OAuth2ManagerInterface only
- Reauth doesn't know about: Amazon vs Google, PKCE details, endpoint URLs
- Coupling: Low (depends on interface, not implementation)

### High Coupling (Bad - Forbidden)

❌ **Storage Implementation depends on Refresh Logic**
- Coupling: Bidirectional, circular
- Problem: Can't change either without affecting both
- Solution: Introduce interface, break cycle

---

## Verification Checklist

For each component, verify:

1. **Does it depend only on more abstract components?**
   - [ ] YES: Dependency direction correct
   - [ ] NO: Violation - need to introduce interface

2. **Can concrete implementation be swapped without changing dependents?**
   - [ ] YES: Inversion applied correctly
   - [ ] NO: Violation - too tightly coupled

3. **Can specification (interface) be changed without affecting users?**
   - [ ] YES: Interface versioning working
   - [ ] NO: Violation - interface too volatile

4. **Are there cycles in the dependency graph?**
   - [ ] NO: Good (acyclic)
   - [ ] YES: Violation - refactor to break cycle

---

## Related Documents

- [System Overview](SYSTEM_OVERVIEW.md) - Component architecture
- [Security Principles](SECURITY_PRINCIPLES.md) - Abstract security rules

---

**Last Updated**: November 1, 2025
**Layer**: 00_ARCHITECTURE (Technology-Agnostic, Most Stable)
**Change Frequency**: Rare (affects entire system design)
