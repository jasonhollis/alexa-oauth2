# ADR-001: Replace Legacy Integration Rather Than Patch

**Date**: 2025-11-02
**Status**: Accepted
**Decision Makers**: Security analysis, architectural review
**Impact**: Breaking change requiring complete migration

---

## Context

The legacy Home Assistant Core `alexa` integration contains three critical security vulnerabilities that fundamentally compromise user account security:

### Security Vulnerabilities

**1. Missing Authorization Code Protection (CVSS 9.1 Critical)**
- **Vulnerability**: OAuth2 authorization codes can be intercepted and exchanged for access tokens
- **Root Cause**: No cryptographic binding between authorization request and token exchange
- **Attack Vector**: Man-in-the-middle attacker intercepts authorization code, exchanges it for valid tokens before legitimate client
- **Impact**: Complete Amazon account compromise

**2. Plaintext Credential Storage**
- **Vulnerability**: OAuth2 access tokens and refresh tokens stored without encryption
- **Root Cause**: Token persistence mechanism writes credentials in cleartext to filesystem
- **Attack Vector**: Any process or user with filesystem read access obtains long-lived Amazon credentials
- **Impact**: Persistent account access, survives password changes (refresh token validity)

**3. Unauthenticated Request Processing**
- **Vulnerability**: Smart home control endpoint processes requests without cryptographic validation
- **Root Cause**: Endpoint trusts network-level isolation, no signature verification
- **Attack Vector**: Any network-reachable client can invoke smart home commands without Amazon authorization
- **Impact**: Unauthorized device control, privacy violation, physical security risk

### Architectural Incompatibility

The legacy integration's architecture is fundamentally incompatible with security remediation:

**Authorization Flow Architecture**:
- Current: Simple redirect → code → token exchange (OAuth2 without extensions)
- Required: Challenge generation → redirect → code → verifier validation → token exchange (OAuth2 + PKCE)
- **Incompatibility**: PKCE requires state management across authorization phases not present in current architecture

**Token Storage Architecture**:
- Current: Direct serialization to JSON storage (`.storage/` mechanism)
- Required: Key derivation → encryption → storage, decryption → use pattern
- **Incompatibility**: No key material available, no decryption hooks in token retrieval paths

**Request Validation Architecture**:
- Current: HTTP endpoint with direct handler invocation
- Required: Signature extraction → cryptographic validation → handler invocation
- **Incompatibility**: Validation must occur before request routing, current architecture routes first

### Business Context

**Migration Inevitability**: Any security fix requires breaking changes:
- PKCE changes authorization URL structure (challenge parameter)
- Token encryption changes storage format (existing tokens unreadable)
- Signature validation changes endpoint contract (requires signed requests)

**Technical Debt**: Legacy integration accumulated 6+ years of incremental changes:
- Inconsistent error handling patterns
- Mixed synchronous/asynchronous code
- Unclear module boundaries
- Difficult to test (tight coupling to Home Assistant internals)

---

## Decision

**We will replace the legacy integration entirely with a new implementation built on security-first architectural principles.**

### Rationale

**1. Architectural Mismatch Prevents Patching**

Patching requires retrofitting security controls into incompatible architecture:
- PKCE state management doesn't map to stateless authorization flow
- Encryption key derivation has no integration point in current token lifecycle
- Signature validation requires request processing order inversion

**2. Migration Costs Equivalent**

Both approaches require breaking changes and user migration:
- Patch approach: Change authorization flow + storage format + endpoint contract
- Replace approach: New integration installation + configuration migration
- **Outcome**: User disruption identical, but replace approach gains architectural foundation

**3. Long-Term Security Posture**

Fresh implementation enables security-first design:
- Defense in depth from initial architecture (not bolted on)
- Clear security boundaries between components
- Testable security controls (unit tests for crypto, validation)
- Future vulnerability remediation easier (clean architecture)

**4. Technical Debt Elimination**

Replace approach removes accumulated complexity:
- Single responsibility modules (not mixed concerns)
- Consistent async patterns (not sync/async mixing)
- Clear error propagation (not scattered exception handling)
- Integration test coverage (not legacy code preservation)

---

## Alternatives Considered

### Alternative 1: Patch Existing Integration (Rejected)

**Approach**: Add PKCE, encryption, and validation to current codebase

**Pros**:
- Preserves configuration file compatibility (maybe)
- Familiar code structure for maintainers
- Incremental security improvements possible

**Cons**:
- **Architectural mismatch**: PKCE requires state management not present in stateless flow
- **Storage incompatibility**: Encryption requires key derivation infrastructure not available
- **Request processing inversion**: Validation before routing requires control flow redesign
- **Technical debt preservation**: Keeps inconsistent patterns, tight coupling
- **Testing difficulty**: Security controls embedded in legacy code hard to isolate
- **Breaking changes anyway**: PKCE changes URLs, encryption changes storage, validation changes endpoint

**Rejection Reason**: Architectural incompatibility makes patching as disruptive as replacement, without gaining clean foundation.

### Alternative 2: Replace with New Implementation (Chosen)

**Approach**: Build new integration from scratch with security-first architecture

**Pros**:
- **Security by design**: PKCE, encryption, validation in initial architecture
- **Clean boundaries**: Separable modules for auth, storage, request handling
- **Testability**: Security controls unit-testable in isolation
- **Future maintainability**: Clear structure simplifies future changes
- **Debt elimination**: No legacy patterns to work around

**Cons**:
- **Development time**: New codebase requires complete implementation
- **Migration complexity**: Users must reconfigure (but required by security fixes anyway)
- **Initial unfamiliarity**: New code structure for maintainers to learn

**Selection Reason**: Equivalent user disruption with superior long-term security and maintainability.

---

## Consequences

### Positive Consequences

**1. Security Foundation**
- OAuth2 + PKCE prevents authorization code interception (eliminates CVSS 9.1 vulnerability)
- Encrypted token storage prevents filesystem credential theft
- Cryptographic request validation prevents unauthorized endpoint access
- Defense-in-depth architecture resists future vulnerability classes

**2. Architectural Quality**
- Clear module boundaries enable independent testing
- Single responsibility components simplify reasoning
- Consistent async patterns improve reliability
- Explicit error handling improves debuggability

**3. Long-Term Maintainability**
- Security controls testable in isolation (unit tests for crypto)
- Future vulnerability fixes don't require architecture redesign
- New features build on clean foundation (not technical debt)
- Code review focuses on logic, not navigating legacy patterns

### Negative Consequences

**1. Migration Disruption**
- **Breaking Change**: Users cannot upgrade in-place, must reconfigure
- **Downtime Window**: Authorization must be re-established with Amazon
- **Configuration Loss**: Existing settings not automatically migrated
- **Documentation Burden**: Migration guide required for user success

**Mitigation**: Provide clear migration documentation, automated configuration detection where possible, validation tools to verify successful migration.

**2. Implementation Time**
- **New Codebase**: Complete OAuth2 + PKCE + encryption + validation implementation
- **Testing Requirements**: Security controls require rigorous test coverage
- **Documentation**: Architecture, usage, and migration documentation

**Mitigation**: Phased implementation (auth first, then storage, then validation), iterative testing, documentation-driven development.

**3. Initial Maintainer Unfamiliarity**
- **New Structure**: Maintainers must learn new codebase organization
- **Security Controls**: Cryptographic validation requires specialized knowledge
- **Testing Patterns**: Security-focused testing different from legacy patterns

**Mitigation**: Comprehensive architecture documentation (this ADR and related docs), inline code documentation, explicit security control design documentation.

### Migration Requirements

**User Actions Required**:
1. Remove legacy integration configuration
2. Install new integration
3. Complete OAuth2 authorization flow (PKCE-enabled)
4. Verify smart home device connectivity
5. Test encrypted token persistence

**Backward Compatibility**: None. Complete replacement, not incremental upgrade.

**Rollback Path**: Revert to legacy integration (but security vulnerabilities remain).

---

## Implementation Phases

**Phase 1: Core Security Controls** (Foundation)
- OAuth2 + PKCE authorization flow
- Encrypted token storage with key derivation
- Cryptographic request signature validation

**Phase 2: Integration Adapters** (Home Assistant Interface)
- Configuration flow integration
- Entity platform registration
- Event handling and state synchronization

**Phase 3: Migration Tooling** (User Experience)
- Configuration detection and validation
- Migration verification tools
- Comprehensive migration documentation

**Phase 4: Testing and Validation** (Quality Assurance)
- Unit tests for security controls
- Integration tests for Home Assistant compatibility
- Security audit and penetration testing

---

## References

**Security Standards**:
- RFC 7636: Proof Key for Code Exchange (PKCE)
- RFC 6749: OAuth 2.0 Authorization Framework
- OWASP Top 10: Cryptographic Failures, Broken Access Control

**Architecture Principles**:
- Clean Architecture: Dependency Rule (business logic independent of frameworks)
- Defense in Depth: Multiple independent security layers
- Least Privilege: Minimize credential exposure and validity duration

**Related Documentation**:
- `00_ARCHITECTURE/SECURITY_PRINCIPLES.md` - Core security requirements
- `01_USE_CASES/AUTHORIZATION_FLOW.md` - User authorization workflow
- `04_INFRASTRUCTURE/TOKEN_ENCRYPTION.md` - Token storage implementation

---

## Decision Validation

**Success Criteria**:
1. ✓ Authorization code interception prevented (PKCE validation)
2. ✓ Filesystem credential theft prevented (encrypted storage)
3. ✓ Unauthorized endpoint access prevented (signature validation)
4. ✓ Security controls unit-testable (isolated test cases)
5. ✓ Future vulnerability remediation simplified (clean architecture)

**Acceptance Tests**:
- Authorization flow completes with PKCE challenge/verifier validation
- Tokens stored encrypted, unreadable without decryption key
- Unsigned requests rejected by smart home endpoint
- Security control unit tests achieve >90% code coverage
- Integration tests verify Home Assistant compatibility

**Review Date**: 2025-11-02 + 6 months (2025-05-02)
- Validate migration success rate
- Assess security incident reports (expect zero credential compromise)
- Evaluate maintainer feedback on architecture quality
