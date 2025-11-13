# ADR-003: User Migration Strategy - YAML to OAuth2

**Layer**: 00_ARCHITECTURE
**Type**: Architectural Decision Record
**Status**: ACCEPTED
**Decided**: November 2, 2025

---

## Context

**Question**: How should 10,000+ users migrate from YAML-configured Alexa integration to OAuth2 integration?

**Critical Constraint**: Users have automations depending on entity IDs (e.g., `light.living_room`). If entity IDs change, all automations break.

**Options Considered**:
1. Manual migration (user deletes old, adds new)
2. Dual support (both YAML and OAuth2 indefinitely)
3. Forced migration (auto-migrate on upgrade)
4. Atomic migration with rollback ← **CHOSEN**

**Risk Factors**:
- **Automation breakage**: Users have hundreds of automations depending on specific entity IDs
- **Downtime tolerance**: Smart home users expect <30 second outages maximum
- **User sophistication**: Many users don't understand YAML vs UI configuration
- **Rollback safety**: If migration fails, user must be able to recover immediately
- **Support burden**: 10,000+ users migrating simultaneously creates support tickets

---

## Decision

**Use atomic all-or-nothing migration with 2-minute rollback window, deployed over 24-month phased timeline.**

### Why Atomic Migration?

✅ **Preserves Entity IDs**
- Entity IDs are the primary key for automations
- `light.living_room` must remain `light.living_room` after migration
- Atomic migration transfers entity registry entries (not create new ones)
- No automation changes required from user

✅ **Zero Downtime Goal**
- Migration completes in 15-30 seconds
- Devices briefly unavailable during entity transfer
- Acceptable for smart home use case (< 30 seconds)
- No multi-hour maintenance windows

✅ **All-or-Nothing Safety**
- Migration either completes fully or reverts fully
- No partial states (some entities migrated, others not)
- User sees either "old system working" or "new system working"
- Reduces support complexity (no debugging partial migrations)

✅ **Rollback Window**
- User has 2 minutes to test new integration
- If issues detected, user clicks "Rollback" button
- System reverts to YAML configuration atomically
- User loses no data, no configuration

### Why Phased Timeline (24 Months)?

✅ **Phase 1 (Months 1-6): Coexistence**
- Both YAML and OAuth2 integrations available
- Users can test OAuth2 without commitment
- No pressure to migrate immediately
- Early adopters identify edge cases

**Goal**: Validate OAuth2 integration stability with 5-10% of user base

✅ **Phase 2 (Months 7-18): Deprecation Warnings**
- YAML configuration shows warnings in UI
- Automated migration tool available
- Documentation encourages migration
- Support prioritizes OAuth2 issues

**Goal**: Migrate 80% of users voluntarily with minimal support burden

✅ **Phase 3 (Months 19-24): Legacy Removal**
- YAML support disabled entirely
- Remaining users force-migrated with notification
- Old code removed from codebase
- Security improvements no longer backported to YAML

**Goal**: Remove technical debt, eliminate security vulnerabilities in old code

### Why 24 Months?

- **User adoption curve**: 10% adopt immediately, 70% adopt gradually, 20% resist change
- **Support capacity**: Support team can handle 100-200 migrations/day comfortably
- **Testing time**: 6 months coexistence identifies critical bugs before deprecation
- **Communication time**: Users need time to read notifications, test, and trust new system

---

## Consequences

### Positive

✅ **User Experience**
- Automations continue working (entity IDs preserved)
- Downtime minimal (15-30 seconds)
- Rollback available (reduces risk perception)
- No manual reconfiguration required

✅ **Support Burden**
- Atomic migration reduces partial-state debugging
- 24-month timeline spreads support load
- Early adopters identify issues before mass migration
- Rollback reduces "migration broke my system" tickets

✅ **Security**
- Users migrate to secure OAuth2 system
- Old YAML integration removed (eliminates credential storage risks)
- No indefinite dual support (reduces attack surface)

✅ **Code Quality**
- Legacy code removed after Phase 3
- No permanent dual-code-path maintenance
- Technical debt eliminated
- Future features only need OAuth2 support

### Tradeoffs

❌ **Implementation Complexity**
- Atomic migration requires careful entity registry manipulation
- Rollback mechanism adds complexity
- Must maintain both integrations during coexistence phase
- Testing matrix larger (YAML, OAuth2, migration path)

❌ **Timeline Length**
- 24 months feels slow for developers
- Security vulnerabilities in YAML code must be maintained
- Users complain about "deprecated" warnings for 18 months
- Code deletion delayed (technical debt persists longer)

❌ **Rollback Window Risk**
- 2-minute window may not be enough for thorough testing
- User may discover issues after rollback window closes
- Must support "manual rollback" for late discoveries
- Additional documentation required for rollback procedures

❌ **Support Load During Deprecation**
- Phase 2 (months 7-18) highest support burden
- Users confused by deprecation warnings
- "Why can't I just keep YAML?" questions
- Must maintain two documentation sets

---

## Alternatives Rejected

### Alternative 1: Manual Migration ❌

**What It Is**: User deletes old YAML integration, manually adds OAuth2 integration

**Why Not**:
- ❌ Entity IDs change (automations break)
- ❌ User must manually reconfigure all entities
- ❌ High support burden (users don't understand process)
- ❌ Users resist migration (perceived as "too much work")
- ❌ No rollback (once YAML deleted, can't restore)

**Example Failure**:
- User has 50 devices, 200 automations
- User deletes YAML integration
- OAuth2 integration creates new entity IDs (`light.living_room_2`)
- All 200 automations now reference non-existent entities
- User must manually edit 200 automations
- User frustrated, leaves bad review

### Alternative 2: Dual Support Indefinitely ❌

**What It Is**: Support both YAML and OAuth2 forever

**Why Not**:
- ❌ Maintenance burden (two code paths forever)
- ❌ Security risk persists (YAML stores credentials insecurely)
- ❌ Users never migrate (no incentive)
- ❌ New features require implementation twice
- ❌ Testing complexity doubles
- ❌ Technical debt never resolved

**Example Failure**:
- Security vulnerability discovered in YAML credential storage
- Must patch both YAML and OAuth2 code paths
- YAML users still at risk (because they never migrated)
- Development team maintains legacy code indefinitely
- New OAuth2 features unavailable to YAML users

### Alternative 3: Forced Migration ❌

**What It Is**: Auto-migrate all users on Home Assistant upgrade

**Why Not**:
- ❌ No rollback (users can't revert if issues occur)
- ❌ Simultaneous migration spike (10,000+ users at once)
- ❌ Support overwhelmed (cannot handle 10,000 tickets simultaneously)
- ❌ Edge cases not identified (no gradual testing phase)
- ❌ User trust damaged ("system forced change on me")

**Example Failure**:
- Home Assistant releases update with forced migration
- 10,000 users upgrade simultaneously
- Migration bug affects 5% of users (500 people)
- Support receives 500 tickets in 24 hours (cannot respond)
- Users post angry Reddit threads
- Home Assistant reputation damaged

---

## Implementation Constraints

### Entity ID Preservation Requirements

**Must Not Change**:
- Entity ID string (e.g., `light.living_room`)
- Entity unique ID (internal registry key)
- Entity state (on/off, brightness, etc.)
- Entity attributes (friendly_name, supported_features, etc.)

**May Change**:
- Configuration source (YAML → UI)
- Authentication mechanism (credentials → OAuth2 tokens)
- Internal platform references (alexa_yaml → alexa_oauth2)

### Rollback Requirements

**Within 2-Minute Window**:
- User clicks "Rollback" button in UI
- System restores YAML configuration atomically
- Entity IDs revert to YAML platform
- No data loss, no state loss

**After 2-Minute Window**:
- Rollback no longer automatic
- User must manually restore YAML configuration
- Entity IDs may not be preserved
- Documentation provides manual rollback steps

### Phased Rollout Requirements

**Phase 1: Coexistence (Months 1-6)**
- Both integrations available
- No warnings, no pressure
- Documentation updated
- Early adopter community identified

**Phase 2: Deprecation (Months 7-18)**
- YAML shows warning banner
- Migration tool available in UI
- Blog post announces deprecation timeline
- Support FAQ updated

**Phase 3: Removal (Months 19-24)**
- YAML disabled by default
- Force-migration with 30-day notice
- Old code removed from codebase
- Security updates stop for YAML

---

## Verification

### Is This Decision Technology-Agnostic?

**Test**: Can we implement this in different system architecture?

✅ YES
- Atomic migration concept applies to any entity registry system
- Rollback window applies to any migration strategy
- Phased timeline applies to any user base migration
- Entity ID preservation is universal requirement

### Can We Change Implementation Without Changing Decision?

**Test**: Can we swap entity registry backend or migration tool?

✅ YES (through interfaces)
- Can use different entity registry (database, files, etc.)
- Can use different rollback mechanism (snapshots, transactions, etc.)
- Implementation details don't affect decision rationale

### Does This Address the Original Constraint?

**Test**: Does this satisfy "preserve entity IDs and minimize downtime"?

✅ YES
- Entity IDs preserved ✅
- Downtime <30 seconds ✅
- Rollback available ✅
- Phased timeline reduces risk ✅

---

## Related Decisions

- [ADR-001: OAuth2 with PKCE](ADR-001-OAUTH2-PKCE.md) - Why OAuth2 (the target of this migration)
- ADR-002 (future): Entity Registry Design - How entity IDs are stored and transferred

## Related Documents

- [System Overview](SYSTEM_OVERVIEW.md) - Overall architecture
- [Security Principles](SECURITY_PRINCIPLES.md) - Why YAML migration necessary for security

---

**Decision Made**: November 2, 2025
**Implementation Status**: Planning phase
**Layer**: 00_ARCHITECTURE (Migration strategy affects entire system)
