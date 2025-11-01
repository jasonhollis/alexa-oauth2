# Alexa OAuth2 Integration - Quick Start

**Purpose**: 30-second orientation for this project
**Read Time**: 30 seconds
**Last Updated**: November 1, 2025

---

## What Is This?

A modernized **Amazon Alexa integration for Home Assistant** that replaces legacy YAML-based setup with secure **OAuth2 authentication**, automatic token refresh, and seamless migration from old system.

---

## Current Status

✅ **Development Complete**
- All 171 tests passing
- OAuth2 with PKCE fully implemented
- Token encryption and automatic refresh working
- HACS packaging configured
- Amazon Alexa Skill setup guide created

⏳ **Beta Testing Phase**
- Real-world OAuth testing with Amazon credentials (NEXT STEP)
- User testing with external testers
- Submission to Home Assistant Core

---

## Next Steps (For You)

1. **Create Amazon Alexa Skill** (15 minutes)
   - Read: `docs/05_OPERATIONS/AMAZON_SKILL_SETUP.md`
   - Go to https://developer.amazon.com/dashboard
   - Create Smart Home Alexa Skill with OAuth2 setup
   - Get Client ID and Client Secret

2. **Test OAuth Flow** (5 minutes)
   - Home Assistant: Settings → Devices & Services → Add Integration
   - Search "Alexa OAuth2"
   - Enter Client ID and Client Secret
   - Verify integration connects successfully

3. **Check Documentation** (2 minutes)
   - Architecture: `docs/00_ARCHITECTURE/SYSTEM_OVERVIEW.md`
   - How it works: `docs/04_INFRASTRUCTURE/OAUTH_IMPLEMENTATION.md`
   - Troubleshooting: `docs/05_OPERATIONS/TROUBLESHOOTING.md`

---

## Project Structure

```
alexa-oauth2/
├── 00_QUICKSTART.md           ← You are here
├── SESSION_RESUMPTION.md      ← Resume after days away
├── PROJECT.md                 ← Project overview
├── SESSION_LOG.md             ← Activity log
├── DECISIONS.md               ← Why these choices?
├── README.md                  ← Public documentation
│
├── custom_components/alexa/   ← Main code
│   ├── __init__.py           ← Entry point
│   ├── oauth_manager.py       ← OAuth2 flow (750 lines)
│   ├── token_manager.py       ← Token storage (570 lines)
│   ├── session_manager.py     ← Background refresh (640 lines)
│   ├── advanced_reauth.py     ← Reauth logic (900 lines)
│   ├── config_flow.py         ← UI setup (840 lines)
│   ├── yaml_migration.py      ← YAML → OAuth2 migration (820 lines)
│   ├── const.py              ← Constants
│   └── exceptions.py          ← Custom exceptions
│
├── tests/                     ← Test suite (4,268 lines, 171 tests)
│
└── docs/                      ← Documentation (Clean Architecture)
    ├── 00_ARCHITECTURE/       ← Technology-agnostic principles
    │   ├── SYSTEM_OVERVIEW.md
    │   ├── DEPENDENCY_RULES.md
    │   ├── SECURITY_PRINCIPLES.md
    │   └── ADR-001-OAUTH2-PKCE.md
    ├── 01_USE_CASES/          ← User workflows
    ├── 02_REFERENCE/          ← Quick lookups
    ├── 03_INTERFACES/         ← API contracts
    ├── 04_INFRASTRUCTURE/     ← Implementation details
    └── 05_OPERATIONS/         ← Setup & troubleshooting
```

---

## Critical Files to Know

### To Understand Architecture
- Read: `docs/00_ARCHITECTURE/SYSTEM_OVERVIEW.md` (15 minutes)
- Then: `docs/00_ARCHITECTURE/DEPENDENCY_RULES.md` (10 minutes)

### To Understand Implementation
- Read: `docs/04_INFRASTRUCTURE/OAUTH_IMPLEMENTATION.md` (15 minutes)
- Then: `docs/04_INFRASTRUCTURE/FILE_STRUCTURE.md` (5 minutes)

### To Set Up Integration
- Read: `docs/05_OPERATIONS/AMAZON_SKILL_SETUP.md` (20 minutes)
- Then: `docs/05_OPERATIONS/INSTALLATION.md` (5 minutes)

### To Troubleshoot Issues
- Read: `docs/05_OPERATIONS/TROUBLESHOOTING.md` (5 minutes, search for your issue)

---

## Quick Command Reference

```bash
# Navigate to project
cd /Users/jason/alexa-oauth2

# Activate virtual environment
source venv/bin/activate

# Run all tests
pytest tests/components/alexa/ -v --cov

# Run specific test file
pytest tests/components/alexa/test_oauth_manager.py -xvs

# Check code coverage
pytest tests/components/alexa/ --cov --cov-report=html
open htmlcov/index.html

# Commit changes
git add .
git commit -m "message"

# Check GitHub Actions
gh run list --limit 5
```

---

## Key Concepts

**OAuth2**: Industry-standard authentication (like "Login with Google")
**PKCE**: Proof that this system (not an attacker) is using the authorization code
**Token**: Credential proving user authorized this system
**Refresh Token**: Long-lived credential used to get new short-lived tokens
**Reauth**: When user needs to re-authorize (token expired/revoked)

---

## Contact Points

- **GitHub Issues**: https://github.com/jasonhollis/alexa-oauth2/issues
- **Code Repository**: https://github.com/jasonhollis/alexa-oauth2
- **HACS Listing**: (after beta completion)

---

## Next: Session Resumption Guide

After you leave this session, read **SESSION_RESUMPTION.md** when you come back. It has:
- Current status
- Next steps
- Critical files
- Common tasks
- Troubleshooting

---

**Ready?** Start with: `docs/05_OPERATIONS/AMAZON_SKILL_SETUP.md`
