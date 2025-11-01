# Abstract Security Principles

**Layer**: 00_ARCHITECTURE (Innermost - Technology Agnostic)
**Purpose**: Define abstract security guarantees independent of implementation
**Audience**: Security architects, decision makers
**Stability**: Highest

---

## Threat Model

**Assumed Threats**:
1. Local filesystem compromise (attacker has read access to storage files)
2. Memory access (attacker can read process memory)
3. Network sniffing (attacker can intercept HTTPS traffic)
4. User environment (attacker has physical access to device)

**Out of Scope** (not addressed by this system):
- Root-level privilege escalation
- Compromised operating system kernel
- Hardware-level attacks (cold boot, side-channel)
- Social engineering (user gives away password)

---

## Security Guarantees

### Guarantee 1: Confidentiality at Rest

**Problem**: Tokens stored on local filesystem. Attacker with file system access can read tokens.

**Guarantee**: Even if attacker reads encrypted token file, cannot extract tokens without encryption key.

**How It Works**:
- Tokens encrypted with authenticated encryption algorithm
- Encryption key derived from non-guessable source (combination of system state)
- Per-installation salt prevents pre-computed rainbow tables
- Algorithm resistant to brute-force key search

**Consequence**: Filesystem compromise does not immediately compromise tokens.

**Verification**:
- Can someone with disk read access extract tokens? NO
- Is key ever written to disk? NO
- Can attacker pre-compute the key? NO (salt unknown)

### Guarantee 2: Integrity in Transit

**Problem**: Tokens transmitted over network. Attacker might intercept or modify.

**Guarantee**: Modified tokens detected. Tampered tokens rejected as invalid.

**How It Works**:
- HTTPS only (encrypted transport)
- Server certificate validation (prevent man-in-middle)
- OAuth2 state parameter (prevent authorization code interception)
- PKCE code challenge (prove authorization code belongs to legitimate client)

**Consequence**: Even if attacker intercepts network traffic, cannot forge valid tokens.

**Verification**:
- Can someone modify tokens in transit? NO (HTTPS prevents it)
- Can someone intercept authorization code? NO (state parameter prevents reuse)
- Can someone use stolen authorization code? NO (PKCE challenge required)

### Guarantee 3: Authentication

**Problem**: System proves it's authentic to OAuth2 provider. Provider authenticates user.

**Guarantee**: Only legitimate system (with knowledge of PKCE verifier) can exchange authorization code for tokens.

**How It Works**:
- PKCE code challenge: Send SHA256(verifier) to provider
- Authorization code received: Valid only with this specific verifier
- Token exchange: Must provide original verifier to get tokens
- Provider validates: Received verifier matches received code challenge

**Consequence**: Attacker stealing authorization code cannot exchange it for tokens (missing verifier).

**Verification**:
- Can stolen authorization code be used? NO (verifier required)
- Can authorization code be used twice? NO (provider invalidates after first use)
- Can different verifier be used? NO (must match code challenge)

### Guarantee 4: Non-Repudiation

**Problem**: User authorizes with provider. System must prove it was legitimate.

**Guarantee**: Authorization is bound to this specific system instance via PKCE.

**How It Works**:
- Verifier is ephemeral (generated per authorization, discarded after)
- Verifier is random (256 bits, cryptographically strong)
- Only this system instance possesses this verifier
- Provider confirms: Only system with this verifier gets tokens

**Consequence**: Authorization cannot be replayed on different system.

**Verification**:
- Can authorization token be used on different device? NO (verifier specific to device)
- Can verifier be reused? NO (ephemeral, discarded after use)
- Can previous authorization be replayed? NO (code valid once only)

---

## Encryption Standards

### At-Rest Encryption

**Threat**: Attacker reads token file from filesystem

**Standard**: Authenticated encryption with associated data (AEAD)

**Requirements**:
- Encryption algorithm: NIST-approved (AES-128 minimum)
- Authentication: HMAC or internal authentication tag
- Mode: Authenticated encryption (CBC + HMAC or Galois/Counter)
- Key length: 128 bits minimum (256 bits optimal)
- IV/Nonce: Random per encryption, stored with ciphertext

**Verification**:
- Can attacker decrypt without key? NO
- Can attacker forge authentication tag? NO
- Can IV be reused? NO (entropy sufficient for random generation)

### Key Derivation

**Threat**: Attacker tries to guess encryption key via brute force

**Standard**: Key derivation function (KDF) with high iteration count

**Requirements**:
- Algorithm: PBKDF2, Argon2, or scrypt
- Iterations: OWASP recommends 600,000+ for PBKDF2
- Salt: Unique per installation (256+ bits)
- Hash function: HMAC-SHA256 or stronger
- Output length: Match encryption key size

**Verification**:
- Can attacker guess key faster than offline dictionary? NO (iterations too many)
- Can attacker pre-compute keys? NO (salt unique per device)
- Is KDF resistant to GPU attacks? YES (iteration count prevents GPU parallelization)

### Token Storage Format

**Threat**: Attacker reads encrypted token file

**Format**: `[salt || IV || ciphertext || auth_tag]`

**Properties**:
- Salt: 256 bits, unique per installation
- IV: 128 bits, random per encryption
- Ciphertext: Variable length (token data)
- Auth tag: 128 bits (authentication code)

**Verification**:
- Is salt stored with ciphertext? YES (needed for key derivation)
- Is IV stored? YES (needed for decryption)
- Can auth tag be removed? NO (decryption fails)
- Can tokens be mixed? NO (each has unique IV)

---

## Token Lifecycle Security

### Token Generation

**Guarantee**: Tokens received from trustworthy source

**How It Works**:
- OAuth2 provider authenticates user
- Provider validates client (PKCE challenge)
- Provider issues tokens tied to specific client
- Tokens include expiry time

**Consequence**: Tokens valid only for this system, expire automatically.

### Token Storage

**Guarantee**: Tokens encrypted before writing to disk

**How It Works**:
- Tokens obtained from provider
- Encryption key derived (if not already derived)
- Tokens encrypted with AEAD
- Encrypted tokens written to storage
- Original tokens cleared from memory

**Consequence**: Disk access doesn't compromise tokens.

### Token Retrieval

**Guarantee**: Decryption only performed on trusted system

**How It Works**:
- Caller requests current token
- System checks expiry
- If not expired: decrypt and return
- If expired: refresh tokens
- Original token cleared after use

**Consequence**: Token lives in memory only when needed.

### Token Refresh

**Guarantee**: Refresh tokens handled with same security as access tokens

**How It Works**:
- Refresh token stored encrypted (same as access token)
- Used only to request new access token
- Communication over HTTPS (encryption in transit)
- New tokens encrypted and stored
- Old refresh token may be revoked or retained per provider

**Consequence**: Refresh process doesn't expose credentials.

### Token Revocation

**Guarantee**: When integration removed, tokens destroyed

**How It Works**:
- User removes integration from Home Assistant
- Revocation request sent to OAuth2 provider
- Local token file deleted
- Encryption key discarded
- Tokens cannot be recovered

**Consequence**: Removing integration guarantees token destruction.

---

## Operational Security

### Secret Handling

**Principle**: Client secret (if used) never stored on device

**How It Works**:
- OAuth2 with PKCE requires no client secret on device
- Authorization code + PKCE verifier sufficient
- Proves legitimate client without storing secret

**Consequence**: No client secret to leak if device compromised.

### Key Management

**Principle**: Encryption key derived, never transmitted or stored

**How It Works**:
- Key derived from combination of stable system properties
- Same input always produces same key (deterministic)
- Different device produces different key
- Key discarded after use, re-derived on next access

**Consequence**: Key never vulnerable to network interception.

### Error Messages

**Principle**: Errors don't leak sensitive information

**How It Works**:
- Generic error messages to user ("Token refresh failed")
- Detailed errors in logs only (for debugging)
- No token values in error messages
- No encryption key in error messages

**Consequence**: Eavesdropper can't infer secrets from errors.

---

## Compliance & Standards

### Standards Referenced

- **RFC 6749**: OAuth 2.0 Authorization Framework
- **RFC 7636**: Proof Key for Code Exchange (PKCE)
- **OWASP**: Password Storage Cheat Sheet
- **NIST**: SP 800-132 (Key Derivation)
- **NIST**: SP 800-38D (Authenticated Encryption GCM)

### Security Testing

**Verification Approach**:
- [ ] Encryption algorithm confirmed NIST-approved
- [ ] Key derivation iteration count verified OWASP minimum
- [ ] PKCE implementation verified RFC 7636 compliant
- [ ] Authenticated encryption implementation verified
- [ ] No secrets logged or exposed in error messages
- [ ] Tokens cleared from memory after use
- [ ] Key derivation uses sufficient entropy

---

## Security Boundaries

### What This System Protects Against

✅ Filesystem compromise (encrypted tokens unreadable)
✅ Network eavesdropping (HTTPS + PKCE)
✅ Token forgery (authenticated encryption)
✅ Authorization code theft (PKCE requires verifier)
✅ Token tampering (HMAC + authentication tag)

### What This System Does NOT Protect Against

❌ Compromised operating system (kernel-level access)
❌ Root privilege escalation (OS-level privilege compromise)
❌ Physical device theft with root access
❌ User credential compromise (password/biometric theft)
❌ Memory access by privileged process (requires OS compromise first)

---

## Verification Checklist

**Can these principles apply to completely different OAuth2 provider?**
- [ ] YES - Principles provider-agnostic (any OIDC/OAuth2 compliant)

**Can encryption algorithm be swapped?**
- [ ] YES - Any NIST-approved AEAD algorithm works

**Can key derivation function be swapped?**
- [ ] YES - Any KDF with sufficient iterations works

**Are security guarantees technology-agnostic?**
- [ ] YES - Based on cryptographic principles, not implementation

---

## Related Documents

- [System Overview](SYSTEM_OVERVIEW.md) - Architecture overview
- [ADR-001: OAuth2 PKCE](ADR-001-OAUTH2-PKCE.md) - Why these standards?

---

**Last Updated**: November 1, 2025
**Layer**: 00_ARCHITECTURE (Technology-Agnostic, Most Stable)
**Change Frequency**: Rare (security principles change infrequently)
