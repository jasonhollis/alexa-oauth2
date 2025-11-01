# System Architecture Overview

**Layer**: 00_ARCHITECTURE (Innermost - Technology Agnostic)
**Purpose**: High-level component architecture and design principles
**Audience**: Architects, decision makers, long-term vision
**Stability**: Highest (changes rarely, affect entire system)

---

## Architecture Principles

### Separation of Concerns

The system is divided into four independent responsibility domains:

1. **Authentication Domain**
   - Responsibility: Secure user identity verification
   - Principle: Exchange user credentials for cryptographic tokens
   - Independence: Can authenticate with any OAuth2 provider

2. **Token Lifecycle Domain**
   - Responsibility: Token validity management
   - Principle: Tokens expire; must refresh before expiry
   - Independence: Works with any token format (JWT, opaque, etc.)

3. **Session Management Domain**
   - Responsibility: Background operation supervision
   - Principle: Tokens refresh automatically without user intervention
   - Independence: Works with any authentication mechanism

4. **Configuration Domain**
   - Responsibility: User setup and preferences
   - Principle: Users configure authentication credentials once
   - Independence: Can support multiple authentication providers

### Dependency Inversion

High-level policies depend on abstractions, not low-level concretions:

```
Authentication Policy (abstract)
    ↑
    | depends on
    ↓
Token Manager Interface (abstraction)
    ↑
    | implemented by
    ↓
Encrypted Token Storage (concrete implementation)
```

**Principle**: Replace encrypted storage with database storage without changing authentication policy.

### Graceful Degradation

The system never fails unexpectedly:

- **Token refresh fails** → Attempt reauth, use stale token if reauth unavailable
- **Network unavailable** → Retry with exponential backoff, notify user
- **Session task crashes** → Restart automatically, monitor health
- **User denies auth** → Clear state, allow retry, no orphaned resources

---

## Component Hierarchy

### Layer 1: User Authorization

**Purpose**: Securely exchange user credentials for tokens
**Responsibility**: Handle OAuth2 flow with cryptographic proof-of-work
**Independence**: No dependency on storage mechanism

**Guarantees**:
- User identity verified by trusted provider (Amazon)
- Tokens obtained through industry-standard flow (OAuth2)
- CSRF protection via state parameter
- Client verification via PKCE code challenge

### Layer 2: Token Management

**Purpose**: Safely store and retrieve tokens
**Responsibility**: Encrypt tokens at rest, provide access to valid tokens
**Independence**: Works with any token format

**Guarantees**:
- Tokens encrypted with authenticated encryption (prevents tampering)
- Key derived from stable source (prevents guessing)
- Per-installation salt (prevents pre-computed attacks)
- Never exposes raw tokens to disk

### Layer 3: Session Supervision

**Purpose**: Keep tokens fresh without user intervention
**Responsibility**: Monitor token expiry, refresh proactively
**Independence**: Works with any refresh mechanism

**Guarantees**:
- Tokens refreshed before expiry (5-minute buffer)
- Single-flight pattern prevents concurrent refresh attempts
- Failures don't cascade (exponential backoff, graceful degradation)
- Background operation transparent to user

### Layer 4: User Configuration

**Purpose**: Collect authentication credentials from user
**Responsibility**: UI flow for OAuth provider setup
**Independence**: Can support any OAuth2 provider

**Guarantees**:
- Credentials captured once via standard UI
- Credentials validated before saving
- Clear error messages for misconfigurations
- Atomic transactions (all-or-nothing setup)

---

## Data Flow

### Successful Authentication Path

```
1. User initiates setup (Config UI)
   ↓
2. Generate cryptographic challenge (PKCE verifier + code challenge)
   ↓
3. Redirect to OAuth2 provider
   ↓
4. User authenticates with provider (Amazon)
   ↓
5. Provider redirects back with authorization code
   ↓
6. Exchange code + challenge for tokens (proves possession of challenge)
   ↓
7. Encrypt tokens with derived key (authenticated encryption)
   ↓
8. Store encrypted tokens in persistent storage
   ↓
9. Setup complete, User sees success
```

### Token Refresh Path

```
1. Background task checks token expiry (every 60 seconds)
   ↓
2. If expiry < 5 minutes away: request refresh
   ↓
3. Exchange refresh token for new access token
   ↓
4. Encrypt new tokens with same derived key
   ↓
5. Replace stored tokens atomically
   ↓
6. Next API call uses fresh token (transparent to user)
```

### Token Failure Path

```
1. Token refresh fails (network error, token revoked, etc.)
   ↓
2. Retry with exponential backoff (5s, 10s, 20s)
   ↓
3. After 3 failed attempts: trigger reauth flow
   ↓
4. User prompted: "Please re-authenticate with Amazon"
   ↓
5. Upon success: tokens replaced, session continues
```

---

## Quality Attributes

### Security

**Threat Model**: Compromise of local storage filesystem

**Mitigations**:
- Tokens encrypted at rest (prevents offline token theft)
- Key derived from stable source (prevents brute-force key guessing)
- Per-installation salt (prevents rainbow table attacks)
- Authenticated encryption (prevents tampering with encrypted tokens)
- Client verification via PKCE (prevents authorization code interception)

### Reliability

**Failure Modes**:
- Network unavailable during authentication
- Token provider service downtime
- User denies authorization
- Stored token becomes invalid

**Recovery Mechanisms**:
- Exponential backoff retry for transient failures
- Reauth flow for persistent failures
- Graceful degradation (use stale token if refresh fails)
- Clear user notification of status

### Maintainability

**Design Principles**:
- Single Responsibility: Each component has one reason to change
- Dependency Inversion: High-level policies don't depend on concrete implementations
- Testability: Each component can be tested in isolation
- Clarity: Component boundaries and responsibilities obvious

---

## Design Patterns

### Single-Flight Pattern

**Purpose**: Prevent duplicate refresh requests when multiple components check token expiry simultaneously

**Mechanism**:
- First component enters critical section (lock)
- Performs refresh operation
- Other components wait for lock release
- Upon release, reuse refreshed token (don't re-refresh)

**Consequence**: N concurrent requests result in 1 token refresh (not N refreshes)

### Exponential Backoff

**Purpose**: Reduce load on failing service while retrying

**Mechanism**:
- First retry: wait 5 seconds
- Second retry: wait 10 seconds
- Third retry: wait 20 seconds
- Give up after 3rd failure

**Consequence**: Temporary service degradation recovers without overwhelming provider

### Graceful Degradation

**Purpose**: Never fail when alternatives exist

**Mechanism**:
- If token refresh fails: use existing token (may be expired but might work)
- If reauth fails: show error but don't erase configuration
- If background task crashes: restart automatically

**Consequence**: User experiences degradation (slower response) rather than complete failure

---

## Architectural Boundaries

### Authentication Domain Boundary

**What it provides**: OAuth2 tokens in exchange for user identity
**What it doesn't provide**: Token storage, expiry management, automatic refresh
**Change scope**: Swap different OAuth2 provider without affecting token management

### Token Lifecycle Domain Boundary

**What it provides**: Encrypted token storage with guaranteed consistency
**What it doesn't provide**: Refresh timing, expiry detection, reauth decisions
**Change scope**: Swap database for encrypted file storage without affecting refresh logic

### Session Domain Boundary

**What it provides**: Background supervision of token freshness
**What it doesn't provide**: Token encryption, OAuth2 flow, user interface
**Change scope**: Change refresh interval (60s → 30s) without affecting other components

### Configuration Domain Boundary

**What it provides**: User setup UI and validation
**What it doesn't provide**: Token exchange, storage, or refresh
**Change scope**: Change UI framework without affecting authentication logic

---

## Evolution Path

### Phase 1: OAuth2 Core ✅
- Authentication domain fully functional
- Token lifecycle fully functional
- Manual testing of credentials entry

### Phase 2: Session Management ✅
- Background refresh task
- Automatic token refresh without user action
- Integration testing with background tasks

### Phase 3: YAML Migration ✅
- Migration domain (convert legacy format)
- Atomic transactions with rollback
- Backward compatibility preserved

### Phase 4: Device Discovery (Future)
- Expand from authentication-only to device enumeration
- Fetch devices from provider
- Display to user for smart home control

### Phase 5: State Synchronization (Future)
- Expand from read-only to bidirectional
- Report device state changes back to provider
- Execute commands from provider

---

## Verification Checklist

**Can these principles apply to completely different tech stack?**
- [ ] Swap Python for Go? YES - Components transfer directly
- [ ] Swap asyncio for threads? YES - Principles apply to any concurrency model
- [ ] Swap local storage for cloud? YES - Encryption/authentication principles unchanged
- [ ] Swap OAuth2 for SAML? YES - Authentication domain is provider-agnostic
- [ ] Swap Amazon for any OAuth2 provider? YES - Interface contract unchanged

**If answers aren't all YES, this layer contains implementation details (violation).**

---

## Related Architecture Documents

- [Dependency Rules](DEPENDENCY_RULES.md) - Component dependency constraints
- [Security Principles](SECURITY_PRINCIPLES.md) - Abstract security guarantees
- [ADR-001: OAuth2 PKCE](ADR-001-OAUTH2-PKCE.md) - Why OAuth2 with PKCE?

---

**Last Updated**: November 1, 2025
**Layer**: 00_ARCHITECTURE (Technology-Agnostic, Most Stable)
**Change Frequency**: Rare (affects entire system)
