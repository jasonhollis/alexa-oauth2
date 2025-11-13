# Alexa OAuth2 Integration - Beta Tester Recruitment Guide

**Status**: Week 2 - Beta Testing Phase
**Target**: 50 beta testers
**Duration**: 2-3 weeks
**Release**: v1.0.0-beta1

---

## Quick Start for Beta Testers

### Prerequisites
- Home Assistant Core 2024.10.0 or later
- Existing Alexa account (Amazon)
- HACS (Home Assistant Community Store) installed
- Working internet connection

### Installation Steps

#### Step 1: Add Custom Repository to HACS
1. Open Home Assistant
2. Go to **Settings → Devices & Services → Custom Repositories**
3. Add this URL: `https://github.com/jasonhollis/alexa-oauth2`
4. Select **Integration** as the category
5. Click **Create**

#### Step 2: Install via HACS
1. Go to **HACS → Integrations**
2. Search for "Alexa OAuth2"
3. Click on it and select **Download**
4. Choose version `1.0.0-beta1`
5. Follow the prompts to complete installation
6. Restart Home Assistant

#### Step 3: Configure the Integration
1. Go to **Settings → Devices & Services → Integrations**
2. Click **Create Integration** (bottom right)
3. Search for "Alexa OAuth2"
4. You'll be redirected to Amazon to authorize
5. Grant permission to access your Alexa account
6. Return to Home Assistant to complete setup

#### Step 4: Verify Installation
1. Check that new Alexa entities appear in your configuration
2. Look for `sensor.alexa_*` or `switch.alexa_*` entities
3. Confirm devices are syncing from your Amazon account

---

## For Current Alexa Users (YAML Migration)

If you're upgrading from the built-in Home Assistant Alexa integration:

### Automatic Migration
- The integration will detect your existing `configuration.yaml` setup
- A wizard will guide you through secure migration
- Your devices and automations will continue working
- **No data loss** - atomic transaction guarantees

### Step-by-Step Migration
1. Follow installation steps above
2. Accept the **Migrate Existing Configuration** prompt
3. Review the migration summary
4. Confirm to proceed
5. Your YAML config will be backed up as `configuration.yaml.alexa_backup`
6. OAuth2 tokens will be securely encrypted and stored

---

## What to Test

### Core Functionality
- [ ] Integration installs without errors
- [ ] Authorization flow works (redirect to Amazon)
- [ ] Devices sync from Alexa app
- [ ] Can enable/disable Alexa devices
- [ ] Device state updates reflect changes in Alexa app

### Advanced Features
- [ ] Token refresh works silently (check logs for "Token refreshed")
- [ ] App revocation is handled gracefully (triggers re-auth)
- [ ] Regional endpoints work (if using EU or Japan)
- [ ] Concurrent requests don't cause token conflicts
- [ ] Integration survives Home Assistant restarts

### Migration Testing (if applicable)
- [ ] Existing YAML configuration is detected
- [ ] Migration wizard appears on first setup
- [ ] Configuration is successfully migrated
- [ ] Backup file is created (`configuration.yaml.alexa_backup`)
- [ ] Automations continue working after migration

### Edge Cases
- [ ] Long period without Home Assistant running (7+ days)
- [ ] Rapid Home Assistant restarts
- [ ] Network disconnections during token refresh
- [ ] Deleting integration and reinstalling
- [ ] Multiple Home Assistant instances with same account

---

## Reporting Issues

### Creating a Bug Report

**Location**: https://github.com/jasonhollis/alexa-oauth2/issues

**Include**:
1. **Home Assistant Version**: Settings → About
2. **Integration Version**: `1.0.0-beta1`
3. **Python Version**: 3.12+
4. **Steps to Reproduce**: Exact sequence that caused the issue
5. **Expected Behavior**: What should have happened
6. **Actual Behavior**: What actually happened
7. **Logs**: Full stack trace from Home Assistant logs (Settings → Developer Tools → Logs)
8. **Screenshots**: Visual representation of the issue (if applicable)

### Example Good Bug Report

```markdown
## Description
Token refresh fails after 24 hours of idle Home Assistant

## Environment
- Home Assistant Version: 2024.10.0
- Integration Version: 1.0.0-beta1
- Python Version: 3.12
- OS: Ubuntu 24.04

## Steps to Reproduce
1. Install and configure integration
2. Let Home Assistant run idle for 24+ hours
3. Wake system or trigger a HA restart
4. Check integration status

## Expected Behavior
Token should refresh silently, integration should be ready

## Actual Behavior
Integration shows "Unauthorized" status, requires manual re-auth

## Logs
[Paste full stack trace from HA logs]
```

### Example Good Feature Request

```markdown
## Problem Statement
It would be helpful to have a service for controlling Alexa routines

## Proposed Solution
Add an `alexa_oauth2.execute_routine` service that accepts a routine name

## Use Case
Automatically trigger "Goodnight" routine when security system arms for night
```

---

## Testing Timeline

### Week 2: Beta Launch
- **Days 1-3**: Core functionality validation
  - Test on different hardware (RPi, VM, Intel NUC, etc.)
  - Verify device sync works
  - Check logs for errors

- **Days 4-7**: Advanced features
  - Token refresh behavior
  - App revocation scenarios
  - Regional endpoint testing (if applicable)

### Week 3: Stabilization
- Bug fixes based on reports
- Performance optimization
- Documentation updates
- Final validation run

### Week 4: Release Candidate
- Prepare for Home Assistant Core submission
- Final bug fixes
- Performance benchmarks
- Security audit results

---

## Expected Behavior

### Token Refresh
- Happens automatically in the background
- Logs entry: `"Refreshing Alexa access token (expires in 30m)"`
- No user action required
- Transparent to automations and scripts

### Error Handling
- **Network timeout**: Automatic retry with exponential backoff
- **Invalid refresh token**: Triggers re-authentication prompt
- **API rate limit**: Waits before retrying
- **Unknown error**: Safe fail state, integration remains stable

### Performance
- Initial sync: 5-30 seconds (depends on device count)
- Subsequent syncs: <2 seconds
- Memory usage: <50MB
- CPU impact: Negligible between updates

---

## Troubleshooting Guide

### "Integration Failed to Load"
- Check Home Assistant version (needs 2024.10.0+)
- Verify HACS installation
- Check integration logs for specific errors
- Restart Home Assistant

### "Authorization Failed"
- Confirm Amazon account credentials
- Check internet connection
- Verify redirect URI matches GitHub settings
- Try disabling browser extensions that might interfere

### "Devices Not Syncing"
- Verify Alexa app shows your devices
- Check Home Assistant logs for sync errors
- Ensure proper scopes were granted in Amazon OAuth
- Try re-authorizing integration

### "Token Refresh Error"
- This is normal during network issues
- Integration will retry automatically
- Check internet connectivity
- Restart Home Assistant if error persists

---

## Feedback Channels

### How to Provide Feedback

**GitHub Issues** (for bugs): https://github.com/jasonhollis/alexa-oauth2/issues
- Use for reproducible bugs
- Include detailed reproduction steps
- Attach relevant logs

**GitHub Discussions** (for suggestions): https://github.com/jasonhollis/alexa-oauth2/discussions
- Use for feature ideas
- Use for architecture questions
- Use for general feedback

**Email** (for confidential issues):
- Report security issues privately
- Report performance concerns
- Share detailed device configurations

---

## Security & Privacy

### What Data is Collected?
- **No telemetry** - Integration doesn't phone home
- **No analytics** - No tracking of user behavior
- **Logs only** - Standard Home Assistant logging

### How Are Tokens Stored?
- **Encrypted at rest** using Fernet (AES-128-CBC)
- **PBKDF2 key derivation** with 600,000 iterations
- **HMAC validation** to detect tampering
- **No plaintext storage**

### What Permissions Are Required?
- **`alexa::skills:account_linking`** scope only
- Allows: Checking device status, device count, metadata
- Does NOT allow: Accessing Alexa purchasing, voice history, recipes

---

## FAQ

**Q: Will this work with the official Home Assistant Alexa integration?**
A: No - this replaces it. You can migrate automatically or use the OAuth2 version standalone.

**Q: Do I need an Alexa device?**
A: No - you need an Amazon account, but devices are optional.

**Q: Is this officially supported by Home Assistant?**
A: Currently beta - planning Core submission in Week 4.

**Q: What if I find a bug?**
A: Report it on GitHub Issues with reproduction steps and logs.

**Q: Can I downgrade if something breaks?**
A: Yes - previous YAML config is backed up automatically.

**Q: Will my automations break?**
A: Migration preserves existing automations. Test in beta before production use.

**Q: How often are tokens refreshed?**
A: Automatically 5 minutes before expiry (~55 minutes). Transparent to user.

---

## Thank You!

Your participation in beta testing is invaluable. The Alexa OAuth2 integration wouldn't be possible without dedicated testers like you.

**Next Steps**:
1. Install v1.0.0-beta1 from HACS
2. Test core features (2-3 days)
3. Report issues on GitHub Issues
4. Provide feedback via GitHub Discussions
5. Help improve Home Assistant's Alexa integration

**Questions?** Open a GitHub Discussion or check the documentation.

---

**Integration Repository**: https://github.com/jasonhollis/alexa-oauth2
**Beta Release**: https://github.com/jasonhollis/alexa-oauth2/releases/tag/v1.0.0-beta1
**HACS Page**: https://hacs.xyz/ (search "Alexa OAuth2")

