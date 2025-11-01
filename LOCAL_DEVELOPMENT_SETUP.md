# Local Development Setup

**Created**: 2025-11-01
**Location**: `~/projects/alexa-oauth2/` (LOCAL, not iCloud)

---

## Environment Setup Complete ✅

### 1. Local Repository
- **Location**: `~/projects/alexa-oauth2/`
- **Remote**: `git@github.com:jasonhollis/alexa-oauth2.git`
- **SSH Access**: Configured and tested
- **Branch**: main (up to date)

### 2. Python Environment
- **Python Version**: 3.14.0
- **Virtual Environment**: `~/projects/alexa-oauth2/venv/`
- **Dependencies Installed**: ✅
  - pytest 8.4.2
  - pytest-cov 7.0.0
  - pytest-asyncio 1.2.0
  - cryptography 46.0.3
  - pyyaml 6.0.3
  - mypy 1.18.2
  - flake8 7.3.0

---

## Running Tests Locally

### ⚠️ Home Assistant Required

**Tests require Home Assistant to be installed** to run locally:
```bash
# Activate venv
cd ~/projects/alexa-oauth2
source venv/bin/activate

# Install Home Assistant (LARGE - ~500MB)
pip install homeassistant

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=custom_components/alexa --cov-report=term
```

### Alternative: GitHub CI/CD (Recommended)

**Faster Development Cycle** (as you mentioned: "git testing is much faster"):
1. Make code changes locally
2. Commit and push to GitHub
3. GitHub Actions runs all 171 tests automatically
4. Check results at: https://github.com/jasonhollis/alexa-oauth2/actions

**Pros**:
- No need to install Home Assistant locally (saves 500MB+)
- Faster iteration (no local test setup overhead)
- Same environment as production

**Cons**:
- Harder to debug failures (no local debugger)
- Requires internet connection
- Slight delay waiting for CI/CD

### Hybrid Approach (Best of Both)

**For most development**:
- Push to GitHub and use CI/CD for test validation
- Monitor GitHub Actions for test results

**For debugging specific failures**:
- Install Home Assistant locally only when needed
- Use `pytest -k test_specific_function` to run single test
- Debug with breakpoints/prints

---

## Development Workflow

### Daily Development

```bash
# Navigate to local repo (NOT iCloud!)
cd ~/projects/alexa-oauth2

# Activate virtual environment
source venv/bin/activate

# Make changes to code
# (edit custom_components/alexa/*.py)

# Quick syntax check
flake8 custom_components/alexa/

# Type checking
mypy custom_components/alexa/

# Commit and push (triggers CI/CD tests)
git add .
git commit -m "Description of changes"
git push origin main

# Check GitHub Actions for test results
# https://github.com/jasonhollis/alexa-oauth2/actions
```

### Code Quality Checks (Fast, No HA Required)

```bash
cd ~/projects/alexa-oauth2
source venv/bin/activate

# Syntax/style check (< 1 second)
flake8 custom_components/alexa/

# Type checking (< 5 seconds)
mypy custom_components/alexa/

# Line count stats
wc -l custom_components/alexa/*.py
```

---

## Directory Structure

### Local Development (Fast, No Sync Delays)
```
~/projects/alexa-oauth2/              # LOCAL FILESYSTEM (fast)
├── custom_components/alexa/          # Production code
├── tests/components/alexa/           # Test suite
├── venv/                             # Python virtual environment
├── .git/                             # Git repository
└── README.md                         # Documentation
```

### iCloud Planning Docs (Synced Across Machines)
```
~/Library/Mobile Documents/...
└── Claude Stuff/projects/
    ├── alexa-oauth2/                 # Copy for reference (synced)
    └── AlexaIntegration/             # Planning docs (out of sync)
```

**Use Local for**: All development, testing, commits
**Use iCloud Copy for**: Documentation reference, cross-machine sync

---

## Current Status

### ✅ Complete
- Local repository cloned
- SSH write access configured
- Python 3.14 virtual environment
- Test dependencies installed
- Code quality tools ready (flake8, mypy)

### ⏳ Pending
- **User Action**: Create Amazon Alexa Skill (see AMAZON_SKILL_SETUP.md)
- **User Action**: Test OAuth flow with real Amazon credentials
- **User Action**: Verify token refresh works in production

### 📊 Statistics
- **Production Code**: 4,490 lines (10 files)
- **Test Code**: 4,268 lines (7 files)
- **Total Tests**: 171 tests
- **Coverage**: 90%+
- **All Tests Passing**: ✅ (on GitHub CI/CD)

---

## Next Steps

**Priority 1**: Amazon Alexa Skill Setup
1. Read `AMAZON_SKILL_SETUP.md` in this directory
2. Create skill in Amazon Developer Console
3. Configure OAuth settings
4. Test OAuth flow
5. Verify token refresh

**Priority 2**: Local Testing (Optional)
- Install Home Assistant if needed for debugging
- Only required for local test execution
- GitHub CI/CD handles most testing needs

**Priority 3**: Beta Testing
- Recruit beta testers (see BETA_TESTER_RECRUITMENT.md)
- Collect feedback
- Fix issues
- Prepare for Home Assistant Core submission

---

## Troubleshooting

### "Tests won't run locally"
**Solution**: Install Home Assistant
```bash
cd ~/projects/alexa-oauth2
source venv/bin/activate
pip install homeassistant
pytest tests/ -v
```

### "Git push fails with permission denied"
**Check SSH key**:
```bash
ssh -T git@github.com
# Should show: "Hi jasonhollis! You've successfully authenticated"
```

### "Changes not showing in iCloud copy"
**Expected Behavior**: Local repo (`~/projects/alexa-oauth2`) is separate from iCloud
- Work in local repo for speed
- iCloud copy is for reference only
- Don't edit iCloud copy directly

### "Want to sync local → iCloud"
```bash
cd ~/projects/alexa-oauth2
rsync -av --exclude='.git' --exclude='venv' \
  . "/Users/jason/Library/Mobile Documents/com~apple~CloudDocs/Claude Stuff/projects/alexa-oauth2/"
```

---

**Last Updated**: 2025-11-01
**Machine**: M2 Max MacBook Pro (96GB RAM)
**Python**: 3.14.0
**Status**: Ready for Amazon Skill setup and OAuth testing

---

## Session Context

Created during context recovery session after auto-compact.

**Key Files for Context Recovery**:
- `SESSION_CONTINUITY_SUMMARY.md` (in AlexaIntegration/ iCloud directory)
- `CONTEXT_RESUME.md` (HA Cloud architectural decision)
- `DECISIONS.md` (all architectural decisions)
