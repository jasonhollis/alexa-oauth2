# Migration Strategy: Legacy YAML Alexa → OAuth2 Alexa Integration

**Date**: 2025-11-02
**Status**: Draft - Architectural Decision
**Author**: Claude Code (with user guidance)

---

## Executive Summary

This document outlines the strategy for migrating users from Home Assistant's legacy YAML-based `alexa` integration to the new OAuth2+PKCE replacement. The migration must preserve entity IDs, maintain backward compatibility during transition, and address critical security vulnerabilities in the legacy implementation.

**Key Requirements**:
1. **Zero Breaking Changes**: Existing automations continue working
2. **Security Fix**: Address legacy integration's request validation flaw
3. **Smooth Transition**: 18-24 month deprecation timeline
4. **Rollback Safety**: Users can revert if needed
5. **Nabu Casa Compatibility**: Support both Cloud and self-hosted setups

---

## Background

### Legacy `alexa` Integration (Core)

**Configuration**:
```yaml
# configuration.yaml
alexa:
  smart_home:
    client_id: "amzn1.application-oa2-client.xxx"
    client_secret: "secret"
    endpoint: "https://api.amazonalexa.com/v3/events"
    filter:
      include_domains:
        - light
        - switch
```

**Characteristics**:
- YAML-only configuration (no config flow)
- Exposes `/api/alexa/smart_home` endpoint
- Optional OAuth credentials in YAML
- Works without Nabu Casa Cloud
- **Security flaw**: Doesn't cryptographically validate Amazon requests

**Current User Base**:
- Users with YAML configuration
- Mix of Nabu Casa Cloud and self-hosted HTTPS
- Existing automations depend on entity IDs (e.g., `light.living_room`)

### New OAuth2 Integration (Custom Component → Core Replacement)

**Configuration**:
- UI-based config flow (Settings → Integrations → Add Integration → Alexa)
- OAuth2 Authorization Code flow with PKCE (RFC 7636)
- Automatic token refresh and encrypted storage
- Cryptographically validates all Amazon requests

**Advantages**:
- Fixes security vulnerability (no request validation in legacy)
- Modern OAuth2 best practices (PKCE prevents code interception)
- Encrypted token storage (Fernet + PBKDF2)
- Better UX (no YAML editing, UI-based setup)
- Automatic reauth flow on token expiry

**Nabu Casa Dependency**:
- **OAuth callback routing**: Uses `my.home-assistant.io/redirect/oauth` (requires Nabu Casa OR custom domain)
- **Smart Home endpoint**: Should work without Nabu Casa (like legacy integration)

---

## Migration Phases

### Phase 1: Side-by-Side Installation (Months 1-6)

**Goal**: Allow both integrations to coexist while users test new OAuth implementation.

**Implementation**:
1. **New integration domain**: Deploy as `alexa_oauth2` custom component initially
2. **Separate entity IDs**: New integration creates entities with `_oauth2` suffix
   - Legacy: `light.living_room`
   - New: `light.living_room_oauth2`
3. **YAML still works**: Legacy integration continues functioning
4. **Documentation**: Clear migration guide explaining benefits

**User Experience**:
```
Settings → Integrations
├─ Amazon Alexa (Legacy - YAML)  [Configured]
└─ Amazon Alexa (OAuth2)         [+ Add Integration]
```

**Testing Period**:
- Users can test OAuth integration without removing YAML
- Both integrations operate independently
- Rollback: Just delete OAuth integration, YAML stays

**Success Criteria**:
- 1000+ users migrated successfully
- <1% rollback rate
- No entity ID conflicts
- Positive user feedback

### Phase 2: Migration Tool + Deprecation Warning (Months 7-18)

**Goal**: Encourage migration with automated tooling and deprecation notices.

**Implementation**:

1. **Automatic YAML Import**:
   ```python
   # During OAuth config flow setup
   async def async_step_import(self, import_data: dict) -> FlowResult:
       """Import YAML configuration."""
       # Detect existing YAML config
       if "alexa" in self.hass.data.get(YAML_DOMAIN, {}):
           yaml_config = self.hass.data[YAML_DOMAIN]["alexa"]

           # Pre-fill OAuth flow with YAML credentials
           self.client_id = yaml_config.get("client_id")
           self.client_secret = yaml_config.get("client_secret")

           # Preserve entity filtering config
           self.filter_config = yaml_config.get("filter", {})
   ```

2. **Entity ID Preservation**:
   ```python
   # During OAuth entry creation
   async def async_step_user(self, user_input=None):
       # Check for existing legacy integration
       legacy_entries = self.hass.config_entries.async_entries("alexa")

       if legacy_entries and user_input.get("migrate_entities"):
           # Map legacy entity IDs to new integration
           await self._preserve_entity_ids(legacy_entries[0])
   ```

3. **Deprecation Warning**:
   ```
   ⚠️ DEPRECATION WARNING

   The YAML-based Alexa integration will be removed in Home Assistant 2026.12.
   Please migrate to the new OAuth2-based Alexa integration for:

   ✓ Enhanced security (OAuth2 + PKCE)
   ✓ Automatic token refresh
   ✓ Better error handling
   ✓ Modern UI-based setup

   [Migrate Now] [Learn More] [Dismiss]
   ```

4. **Migration UI**:
   ```
   Settings → Integrations → Amazon Alexa (Legacy)

   ┌─────────────────────────────────────────────────────┐
   │  🔔 Migration Available                             │
   │                                                     │
   │  Switch to the new OAuth2-based Alexa integration  │
   │  with enhanced security and automatic setup.       │
   │                                                     │
   │  ✓ Preserve all entity IDs                         │
   │  ✓ Keep existing automations                       │
   │  ✓ Rollback available if needed                    │
   │                                                     │
   │  [Start Migration] [Not Now]                       │
   └─────────────────────────────────────────────────────┘
   ```

**Success Criteria**:
- 80%+ of active users migrated
- Entity ID preservation working
- Clear rollback path documented

### Phase 3: Core Integration Replacement (Months 19-24)

**Goal**: Replace legacy `alexa` integration entirely with OAuth2 version.

**Implementation**:

1. **Rename integration**: `alexa_oauth2` → `alexa` (domain replacement)
2. **Remove YAML support**:
   ```python
   # __init__.py
   async def async_setup(hass, config):
       """Legacy YAML setup no longer supported."""
       if DOMAIN in config:
           _LOGGER.error(
               "YAML configuration for Alexa is no longer supported. "
               "Please use the UI to configure OAuth2-based Alexa integration. "
               "See: https://www.home-assistant.io/integrations/alexa/"
           )
           return False
       return True
   ```

3. **Final migration**: Auto-import remaining YAML users on upgrade
4. **Documentation update**: Update all references to OAuth2 flow

**Rollback Plan**:
- Archive legacy integration code in `homeassistant/components/alexa_legacy/`
- Document manual re-enable: `custom_components/alexa_legacy/` installation
- Preserve YAML config in `.storage/` backup

---

## Technical Implementation Details

### Entity ID Preservation

**Problem**: Legacy integration creates entities like `light.living_room`. New integration must use exact same IDs.

**Solution**: Registry-based entity ID migration

```python
async def _preserve_entity_ids(self, legacy_entry: ConfigEntry) -> None:
    """Preserve entity IDs from legacy integration."""
    entity_registry = er.async_get(self.hass)

    # Find all entities from legacy integration
    legacy_entities = er.async_entries_for_config_entry(
        entity_registry, legacy_entry.entry_id
    )

    for entity in legacy_entities:
        # Transfer entity to new config entry
        entity_registry.async_update_entity(
            entity.entity_id,
            new_config_entry_id=self.entry.entry_id,
            # Preserve all attributes
            new_unique_id=entity.unique_id,
        )

    _LOGGER.info(
        "Migrated %d entities from legacy Alexa integration",
        len(legacy_entities)
    )
```

### YAML Config Import

**Automatic Detection**:
```python
async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Detect YAML config and trigger import flow."""
    if DOMAIN in config:
        # Trigger config flow import
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_IMPORT},
                data=config[DOMAIN],
            )
        )
    return True
```

**Import Flow**:
```python
async def async_step_import(self, import_data: dict) -> FlowResult:
    """Handle import from YAML."""
    # Extract credentials
    self.client_id = import_data.get("smart_home", {}).get("client_id")
    self.client_secret = import_data.get("smart_home", {}).get("client_secret")

    if not self.client_id or not self.client_secret:
        return self.async_abort(reason="missing_credentials")

    # Pre-fill OAuth flow
    return await self.async_step_user(
        user_input={"migrate_from_yaml": True}
    )
```

### Nabu Casa vs Self-Hosted Support

**OAuth Callback Routing**:
```python
@property
def redirect_uri(self) -> str:
    """Return redirect URI based on setup."""
    # Framework automatically handles:
    # - Nabu Casa Cloud: https://my.home-assistant.io/redirect/oauth
    # - Self-hosted: https://your-domain.com/auth/external/callback
    return config_entry_oauth2_flow.async_get_redirect_uri(self.hass)
```

**Smart Home Endpoint** (works without Nabu Casa):
```python
# Expose /api/alexa/smart_home exactly like legacy integration
hass.http.register_view(AlexaSmartHomeView(config_entry))
```

---

## Rollback Procedures

### During Phase 1-2 (Side-by-Side)

**User wants to revert to YAML**:
1. Delete OAuth2 integration via UI: Settings → Integrations → Amazon Alexa (OAuth2) → Delete
2. YAML config still active, legacy integration continues working
3. No entity ID changes needed

### During Phase 3 (After Replacement)

**User needs legacy integration back**:
1. **Restore YAML config**:
   ```bash
   # Restore from backup
   cp .storage/configuration.yaml.alexa_backup configuration.yaml
   ```

2. **Install legacy as custom component**:
   ```bash
   # Download legacy integration
   cd custom_components
   git clone https://github.com/home-assistant/core.git temp
   cp -r temp/homeassistant/components/alexa alexa_legacy
   rm -rf temp
   ```

3. **Restart Home Assistant**: Legacy integration loads from `custom_components/alexa_legacy/`

---

## Testing Strategy

### Integration Tests

```python
async def test_yaml_import(hass):
    """Test YAML config import."""
    config = {
        "alexa": {
            "smart_home": {
                "client_id": "test_client",
                "client_secret": "test_secret",
                "filter": {"include_domains": ["light"]},
            }
        }
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data=config["alexa"],
    )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"]["client_id"] == "test_client"

async def test_entity_id_preservation(hass, entity_registry):
    """Test entity IDs preserved during migration."""
    # Create legacy entity
    legacy_entry = MockConfigEntry(domain="alexa")
    legacy_entry.add_to_hass(hass)

    entity_registry.async_get_or_create(
        "light",
        "alexa",
        "living_room_unique_id",
        suggested_object_id="living_room",
        config_entry=legacy_entry,
    )

    # Migrate to OAuth2
    oauth_entry = MockConfigEntry(domain="alexa_oauth2")
    await preserve_entity_ids(hass, legacy_entry, oauth_entry)

    # Verify entity ID unchanged
    entity = entity_registry.async_get("light.living_room")
    assert entity.config_entry_id == oauth_entry.entry_id
```

### Beta Testing

**Phase 1 Beta**:
- 100 volunteer users
- Mix of Nabu Casa Cloud and self-hosted
- Track migration success rate
- Collect feedback on UX

**Phase 2 Beta**:
- 1000+ users
- Test automatic YAML import
- Validate entity ID preservation
- Monitor rollback requests

---

## Documentation Requirements

### User-Facing Docs

1. **Migration Guide**: Step-by-step instructions with screenshots
2. **FAQ**: Common questions and troubleshooting
3. **Rollback Instructions**: Clear steps to revert
4. **Benefits**: Why migrate (security, features, UX)

### Developer Docs

1. **ADR (Architecture Decision Record)**: Submit to HA core team
2. **Breaking Changes**: Document in release notes
3. **Code Review**: Address HA core team feedback
4. **Test Coverage**: Ensure 95%+ test coverage

---

## Timeline

| Phase | Duration | Milestone | Users Migrated |
|-------|----------|-----------|----------------|
| **Phase 1**: Side-by-side | Months 1-6 | 1000+ beta users | 5% |
| **Phase 2**: Migration tool | Months 7-18 | Deprecation warning | 80% |
| **Phase 3**: Core replacement | Months 19-24 | Legacy removed | 100% |

**Total Timeline**: 24 months (2 years)

**Key Dates**:
- **Month 0**: Submit ADR to HA core team
- **Month 1**: Beta release as custom component
- **Month 6**: Evaluate beta success
- **Month 7**: Merge to HA core as `alexa_oauth2`
- **Month 12**: Enable deprecation warnings
- **Month 18**: Final migration push
- **Month 24**: Remove legacy integration entirely

---

## Success Metrics

### Phase 1 Success
- ✅ 1000+ users migrated
- ✅ <1% rollback rate
- ✅ Positive user feedback (>80% satisfaction)
- ✅ Zero breaking changes reported

### Phase 2 Success
- ✅ 80%+ of active users migrated
- ✅ Entity ID preservation working (100% success rate)
- ✅ Clear documentation and support

### Phase 3 Success
- ✅ 100% of users migrated or inactive
- ✅ Legacy integration archived
- ✅ No critical bugs in production

---

## Risk Mitigation

### Risk: Users refuse to migrate

**Mitigation**:
- Clear communication about security benefits
- Extended deprecation timeline (24 months)
- Easy rollback process
- Active support during transition

### Risk: Entity ID preservation fails

**Mitigation**:
- Extensive testing before Phase 2
- Rollback button in UI
- Preserve YAML config as backup
- Manual entity ID mapping tool if needed

### Risk: Nabu Casa dependency blocks self-hosted users

**Mitigation**:
- Support both Nabu Casa AND self-hosted OAuth callbacks
- Document custom domain setup for self-hosted users
- Consider OAuth Device Flow as alternative (no callback URL needed)

### Risk: HA core team rejects ADR

**Mitigation**:
- Address all feedback promptly
- Follow HA architecture quality standards
- Demonstrate security improvement
- Provide comprehensive test coverage

---

## Outstanding Decisions

### 1. Should we support OAuth without Nabu Casa?

**Options**:
- **A**: Require Nabu Casa for OAuth callback (simplest, but limits users)
- **B**: Support custom domain OAuth callbacks (complex, more flexible)
- **C**: Implement OAuth Device Flow (no callback needed, different UX)

**Recommendation**: **Option B** - Support custom domains for self-hosted users, maintain feature parity with legacy integration.

### 2. Integration name during transition

**Options**:
- **A**: Deploy as `alexa_oauth2` initially, rename to `alexa` in Phase 3
- **B**: Deploy as `alexa_new`, rename to `alexa` in Phase 3
- **C**: Fork core `alexa` and modify in place (risky)

**Recommendation**: **Option A** - Clear naming, smooth transition.

### 3. YAML backup strategy

**Options**:
- **A**: Auto-backup YAML config to `.storage/` during migration
- **B**: Require users to manually backup before migration
- **C**: Keep YAML config untouched, create new config entry

**Recommendation**: **Option A** + **Option C** - Auto-backup AND preserve original YAML for safety.

---

## Next Steps

1. **Submit ADR**: Create Architecture Decision Record for HA core team
2. **Beta Release**: Deploy as `alexa_oauth2` custom component for testing
3. **Gather Feedback**: Iterate based on beta user feedback
4. **Core Submission**: Submit PR to HA core repository
5. **Documentation**: Create comprehensive migration guide
6. **Monitor**: Track migration success metrics

---

## References

- [Home Assistant ADR Process](https://www.home-assistant.io/developers/architecture/)
- [OAuth2 Integration Guide](https://www.home-assistant.io/integrations/oauth2/)
- [RFC 7636 - PKCE](https://datatracker.ietf.org/doc/html/rfc7636)
- [Entity Registry Documentation](https://developers.home-assistant.io/docs/entity_registry_index)
