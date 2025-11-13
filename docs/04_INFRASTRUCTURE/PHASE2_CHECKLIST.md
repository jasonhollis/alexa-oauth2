# Phase 2 Implementation Checklist

**Purpose**: Step-by-step checklist for Phase 2 implementation
**Timeline**: 2-3 weeks
**Goal**: Device discovery and switch platform working end-to-end

---

## Pre-Implementation

### Environment Setup
- [ ] Phase 1 (OAuth2) complete and tested
- [ ] Virtual environment activated
- [ ] All dependencies installed (`pytest`, `pytest-cov`, `aiohttp`, etc.)
- [ ] Test suite passing for Phase 1

### Documentation Review
- [ ] Read [PHASE2_IMPLEMENTATION_GUIDE.md](PHASE2_IMPLEMENTATION_GUIDE.md) (30 min)
- [ ] Review [PHASE2_CODE_TEMPLATES.md](PHASE2_CODE_TEMPLATES.md) (15 min)
- [ ] Understand Home Assistant `DataUpdateCoordinator` pattern
- [ ] Understand Home Assistant `CoordinatorEntity` pattern

---

## Week 1: Foundation (Models + API Client)

### Day 1: Data Models (`models.py`)

#### Implementation
- [ ] Create file: `custom_components/alexa/models.py`
- [ ] Copy capability constants to `const.py`
- [ ] Implement `Capability` dataclass (frozen, immutable)
  - [ ] Add `type`, `interface`, `version`, `properties` fields
  - [ ] Add `from_dict()` factory method
- [ ] Implement `AlexaDevice` dataclass (mutable)
  - [ ] Add required fields: `id`, `friendly_name`, `capabilities`
  - [ ] Add optional fields: `manufacturer`, `model`, `firmware_version`, `is_reachable`
  - [ ] Add state fields: `is_on`, `brightness`, etc.
  - [ ] Add `device_type` property (determines type from capabilities)
  - [ ] Add `has_capability()` method
  - [ ] Add `get_capability()` method
  - [ ] Add `from_api_response()` factory method

#### Testing
- [ ] Create test file: `tests/components/alexa/test_models.py`
- [ ] Test `Capability.from_dict()` parsing
- [ ] Test `AlexaDevice.from_api_response()` parsing
- [ ] Test `has_capability()` method
- [ ] Test `device_type` property (switch vs light vs unknown)
- [ ] Test missing/optional fields handled correctly
- [ ] Run tests: `pytest tests/components/alexa/test_models.py -xvs`

#### Success Criteria
- [ ] All model tests passing
- [ ] Type hints complete (mypy passes)
- [ ] Factory methods handle missing fields gracefully

---

### Days 2-3: API Client (`api_client.py`)

#### Implementation
- [ ] Create file: `custom_components/alexa/api_client.py`
- [ ] Add API constants to `const.py`:
  - [ ] `ALEXA_API_BASE_URL`
  - [ ] `ENDPOINT_DEVICES`
  - [ ] `ENDPOINT_DIRECTIVE`
  - [ ] Rate limit constants
  - [ ] Retry constants
  - [ ] Timeout constants
- [ ] Define custom exceptions:
  - [ ] `AlexaAPIError` (base)
  - [ ] `AlexaAuthenticationError` (401)
  - [ ] `AlexaRateLimitError` (429)
  - [ ] `AlexaAPITimeout` (timeout)
- [ ] Implement `RateLimiter` class:
  - [ ] Token bucket algorithm
  - [ ] `_refill()` method
  - [ ] `try_acquire()` method (non-blocking)
  - [ ] `acquire()` method (blocking with wait)
- [ ] Implement `AlexaAPIClient` class:
  - [ ] `__init__()`: Store hass, session, create rate limiter
  - [ ] `_request()`: Core HTTP method with retry logic
    - [ ] Rate limiter: Wait for token
    - [ ] Token validation: `session.async_ensure_token_valid()`
    - [ ] HTTP request with timeout
    - [ ] Error handling: 401 → `AlexaAuthenticationError`
    - [ ] Error handling: 429 → `AlexaRateLimitError`
    - [ ] Error handling: timeout → `AlexaAPITimeout`
    - [ ] Retry logic: 3 attempts with exponential backoff (1s, 2s, 4s)
  - [ ] `fetch_devices()`: GET /v1/devices
    - [ ] Call `_request("GET", ENDPOINT_DEVICES)`
    - [ ] Parse `endpoints` array
    - [ ] Convert to `AlexaDevice` objects
    - [ ] Return list
  - [ ] `set_power_state()`: POST /v1/directives
    - [ ] Build directive payload
    - [ ] Call `_request("POST", ENDPOINT_DIRECTIVE, json_data=payload)`
  - [ ] `get_device_state()`: GET /v1/devices/{id}/state (optional)

#### Testing
- [ ] Create test file: `tests/components/alexa/test_api_client.py`
- [ ] Test `RateLimiter`:
  - [ ] Burst allows up to capacity requests
  - [ ] Refill works correctly over time
  - [ ] `acquire()` waits when rate limited
- [ ] Test `AlexaAPIClient`:
  - [ ] Mock OAuth2Session and aiohttp
  - [ ] Test `fetch_devices()` success case
  - [ ] Test `fetch_devices()` with 401 → raises `AlexaAuthenticationError`
  - [ ] Test `fetch_devices()` with 429 → raises `AlexaRateLimitError`
  - [ ] Test `fetch_devices()` with timeout → raises `AlexaAPITimeout`
  - [ ] Test retry logic (fails twice, succeeds on third)
  - [ ] Test `set_power_state()` builds correct payload
- [ ] Run tests: `pytest tests/components/alexa/test_api_client.py -xvs`

#### Success Criteria
- [ ] All API client tests passing
- [ ] Rate limiter working (verified with timing tests)
- [ ] Retry logic working (verified with mock failures)
- [ ] HTTP errors mapped to correct exceptions

---

### Day 4: Testing & Polish

#### Integration Testing
- [ ] Create mock Alexa API server (optional, for manual testing)
- [ ] Test rate limiting with real timing (not just unit tests)
- [ ] Test retry logic with simulated failures
- [ ] Verify exponential backoff timing (1s, 2s, 4s)

#### Code Review
- [ ] Review all code for type hints (mypy passes)
- [ ] Review error handling (all paths covered)
- [ ] Review logging (DEBUG/INFO/WARNING/ERROR appropriate)
- [ ] Review docstrings (all public methods documented)
- [ ] Run full test suite: `pytest tests/components/alexa/ -xvs --cov`

#### Success Criteria
- [ ] 90%+ code coverage for models.py and api_client.py
- [ ] No type errors (mypy passes)
- [ ] All tests passing
- [ ] Code review complete

---

## Week 2: Coordinator + Platform

### Days 5-6: Coordinator (`coordinator.py`)

#### Implementation
- [ ] Create file: `custom_components/alexa/coordinator.py`
- [ ] Add update interval constant to `const.py`: `DEFAULT_UPDATE_INTERVAL = 30`
- [ ] Implement `AlexaDeviceCoordinator`:
  - [ ] Inherit from `DataUpdateCoordinator[dict[str, AlexaDevice]]`
  - [ ] `__init__()`:
    - [ ] Call `super().__init__()` with hass, logger, name, update_interval
    - [ ] Store `api_client`
  - [ ] `_async_update_data()`:
    - [ ] Call `api_client.fetch_devices()` with 30s timeout
    - [ ] Convert list to dict: `{device.id: device for device in devices}`
    - [ ] Handle `AlexaAuthenticationError` → raise `ConfigEntryAuthFailed`
    - [ ] Handle `AlexaRateLimitError` → raise `UpdateFailed`
    - [ ] Handle `asyncio.TimeoutError` → raise `UpdateFailed`
    - [ ] Handle `asyncio.CancelledError` → re-raise
    - [ ] Handle `Exception` → log and raise `UpdateFailed`
  - [ ] `async_set_device_power()`:
    - [ ] Call `api_client.set_power_state()`
    - [ ] Call `self.async_request_refresh()` for immediate update

#### Testing
- [ ] Create test file: `tests/components/alexa/test_coordinator.py`
- [ ] Test coordinator with mocked API client:
  - [ ] Test successful update populates `coordinator.data`
  - [ ] Test `AlexaAuthenticationError` → raises `ConfigEntryAuthFailed`
  - [ ] Test `AlexaRateLimitError` → raises `UpdateFailed`
  - [ ] Test timeout → raises `UpdateFailed`
  - [ ] Test cancellation → re-raises `CancelledError`
  - [ ] Test `last_update_success` flag
  - [ ] Test data structure is `dict[str, AlexaDevice]`
- [ ] Run tests: `pytest tests/components/alexa/test_coordinator.py -xvs`

#### Success Criteria
- [ ] All coordinator tests passing
- [ ] Error handling works correctly (auth vs temporary failures)
- [ ] Data structure correct (dict for O(1) lookups)
- [ ] Coordinator updates at correct interval

---

### Days 7-8: Switch Platform (`switch.py`)

#### Implementation
- [ ] Create file: `custom_components/alexa/switch.py`
- [ ] Implement `async_setup_entry()`:
  - [ ] Get coordinator from `hass.data[DOMAIN][entry.entry_id]["coordinator"]`
  - [ ] Create `AlexaSwitchEntity` for each device with `CAPABILITY_POWER`
  - [ ] Call `async_add_entities(entities)`
- [ ] Implement `AlexaSwitchEntity`:
  - [ ] Inherit from `CoordinatorEntity[AlexaDeviceCoordinator]` and `SwitchEntity`
  - [ ] `__init__()`:
    - [ ] Call `super().__init__(coordinator)`
    - [ ] Store `_device_id`
    - [ ] Set `_attr_unique_id = f"{DOMAIN}_switch_{device_id}"`
  - [ ] `@property device`: Return `self.coordinator.data[self._device_id]`
  - [ ] `@property name`: Return `self.device.friendly_name`
  - [ ] `@property is_on`: Return `self.device.is_on`
  - [ ] `@property available`: Check coordinator success + device in data + device reachable
  - [ ] `@property device_info`: Return `DeviceInfo` for device registry
  - [ ] `async_turn_on()`: Call `coordinator.async_set_device_power(device_id, True)`
  - [ ] `async_turn_off()`: Call `coordinator.async_set_device_power(device_id, False)`
  - [ ] `@callback _handle_coordinator_update()`: Call `self.async_write_ha_state()`

#### Testing
- [ ] Create test file: `tests/components/alexa/test_switch.py`
- [ ] Test switch entity:
  - [ ] Test entity creation with correct unique_id
  - [ ] Test `is_on` reflects device state
  - [ ] Test `available` when coordinator success + device reachable
  - [ ] Test `available = False` when device unreachable
  - [ ] Test `available = False` when coordinator failed
  - [ ] Test `async_turn_on()` calls coordinator
  - [ ] Test `async_turn_off()` calls coordinator
  - [ ] Test `device_info` populated correctly
  - [ ] Test entity updates when coordinator data changes
- [ ] Run tests: `pytest tests/components/alexa/test_switch.py -xvs`

#### Success Criteria
- [ ] All switch tests passing
- [ ] Entities created for devices with PowerController
- [ ] turn_on/turn_off work correctly
- [ ] Availability logic correct
- [ ] Device registry integration works

---

### Day 9: Integration Updates (`__init__.py`)

#### Implementation
- [ ] Update `custom_components/alexa/__init__.py`:
  - [ ] Add imports: `api_client`, `coordinator`, `Platform.SWITCH`, `timedelta`
  - [ ] Update `PLATFORMS = [Platform.SWITCH]`
  - [ ] In `async_setup_entry()`:
    - [ ] After OAuth session creation, create `AlexaAPIClient`
    - [ ] Create `AlexaDeviceCoordinator` with 30s interval
    - [ ] Call `coordinator.async_config_entry_first_refresh()`
    - [ ] Store `api_client` and `coordinator` in `hass.data`
    - [ ] Forward to platforms: `await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)`
  - [ ] In `async_unload_entry()`:
    - [ ] Unload platforms
    - [ ] Clean up `hass.data`

#### Testing
- [ ] Create test file: `tests/components/alexa/test_init.py` (update existing)
- [ ] Test integration setup:
  - [ ] Test coordinator created and stored in hass.data
  - [ ] Test API client created and stored in hass.data
  - [ ] Test platforms forwarded (switch)
  - [ ] Test first refresh called
  - [ ] Test setup failure if first refresh fails
- [ ] Test integration unload:
  - [ ] Test platforms unloaded
  - [ ] Test hass.data cleaned up
- [ ] Run tests: `pytest tests/components/alexa/test_init.py -xvs`

#### Success Criteria
- [ ] Integration setup works end-to-end
- [ ] Coordinator created and first refresh called
- [ ] Platforms forwarded correctly
- [ ] Unload cleans up properly

---

## Week 3: Polish + Testing

### Days 10-11: Integration Testing

#### Manual Testing with Home Assistant
- [ ] Install integration in test HA instance
- [ ] Configure with real Alexa credentials (from Phase 1)
- [ ] Verify devices discovered and displayed
- [ ] Verify switches turn on/off correctly
- [ ] Verify entities show correct state
- [ ] Verify entities marked unavailable on errors
- [ ] Test rate limiting (make many requests quickly)
- [ ] Test error scenarios:
  - [ ] Disconnect network → entities unavailable
  - [ ] Invalid token → reauth triggered
  - [ ] Rate limit hit → entities unavailable, then recover

#### End-to-End Tests
- [ ] Create test file: `tests/components/alexa/test_integration.py`
- [ ] Test full flow:
  - [ ] OAuth setup (Phase 1)
  - [ ] Device discovery (Phase 2)
  - [ ] Switch entity creation (Phase 2)
  - [ ] turn_on command end-to-end
  - [ ] turn_off command end-to-end
  - [ ] Coordinator polling updates entities
  - [ ] Auth error triggers reauth
  - [ ] Rate limit recovers gracefully

#### Performance Testing
- [ ] Test with 1 device
- [ ] Test with 10 devices
- [ ] Test with 50 devices (if available)
- [ ] Measure coordinator update time
- [ ] Verify rate limiting prevents 429 errors
- [ ] Verify memory usage stable over time

#### Success Criteria
- [ ] End-to-end flow works in real HA
- [ ] All devices discovered
- [ ] All switches functional
- [ ] Error handling graceful
- [ ] Performance acceptable (< 5s for 50 devices)

---

### Days 12-13: Documentation + Code Review

#### Documentation
- [ ] Update `README.md` with Phase 2 features
- [ ] Update `docs/00_ARCHITECTURE/SYSTEM_OVERVIEW.md` with device discovery
- [ ] Create `docs/01_USE_CASES/DEVICE_CONTROL.md`
- [ ] Update `docs/04_INFRASTRUCTURE/FILE_STRUCTURE.md` with new files
- [ ] Create `docs/05_OPERATIONS/TROUBLESHOOTING.md` Phase 2 section
- [ ] Update `SESSION_LOG.md` with Phase 2 completion

#### Code Review
- [ ] Review all code for:
  - [ ] Type hints complete (mypy passes)
  - [ ] Error handling comprehensive
  - [ ] Logging appropriate
  - [ ] Docstrings complete
  - [ ] No blocking calls in async code
  - [ ] No mutable default arguments
  - [ ] unique_id stable across restarts
  - [ ] Constants defined in const.py
  - [ ] Imports organized correctly
- [ ] Run static analysis:
  - [ ] `mypy custom_components/alexa/`
  - [ ] `ruff check custom_components/alexa/`
  - [ ] `pylint custom_components/alexa/`

#### Coverage Analysis
- [ ] Run coverage: `pytest tests/components/alexa/ --cov --cov-report=html`
- [ ] Review coverage report: `open htmlcov/index.html`
- [ ] Ensure 90%+ coverage for all modules
- [ ] Add tests for uncovered lines

#### Success Criteria
- [ ] Documentation updated and accurate
- [ ] Code passes static analysis
- [ ] 90%+ test coverage
- [ ] All type hints correct

---

### Day 14: Release Preparation

#### Final Testing
- [ ] Run full test suite: `pytest tests/components/alexa/ -xvs --cov`
- [ ] Test in fresh HA instance (clean install)
- [ ] Test upgrade from Phase 1 to Phase 2
- [ ] Verify no breaking changes

#### Release Artifacts
- [ ] Update `CHANGELOG.md` with Phase 2 changes
- [ ] Update version in `manifest.json` (e.g., 0.2.0)
- [ ] Create release notes
- [ ] Tag release: `git tag -a v0.2.0 -m "Phase 2: Device Discovery"`
- [ ] Push tag: `git push origin v0.2.0`

#### Communication
- [ ] Update GitHub README
- [ ] Create GitHub release
- [ ] Notify beta testers (if any)
- [ ] Update project boards

#### Success Criteria
- [ ] All tests passing
- [ ] Release tagged and published
- [ ] Documentation complete
- [ ] Beta testers notified

---

## Final Verification

### Functional Requirements
- [ ] Devices discovered from Alexa API
- [ ] Switch entities created for PowerController devices
- [ ] turn_on command works
- [ ] turn_off command works
- [ ] Entity state updates automatically (30s polling)
- [ ] Entities marked unavailable on errors
- [ ] Reauth triggered on 401 errors

### Non-Functional Requirements
- [ ] Performance: < 5s to fetch 50 devices
- [ ] Reliability: Graceful error handling, no crashes
- [ ] Maintainability: Clean code, well documented
- [ ] Testability: 90%+ coverage, comprehensive tests
- [ ] Security: No tokens logged, rate limiting works

### Code Quality
- [ ] Type hints: 100% coverage
- [ ] Tests: 90%+ coverage
- [ ] Documentation: Complete and accurate
- [ ] Static analysis: No warnings
- [ ] No blocking calls: All async
- [ ] Error handling: Comprehensive

---

## Post-Implementation

### Next Steps (Phase 3)
- [ ] Light platform (brightness, color)
- [ ] Sensor platform (temperature, humidity)
- [ ] Lock platform
- [ ] Climate platform (thermostat)
- [ ] More device types as needed

### Maintenance
- [ ] Monitor GitHub issues
- [ ] Respond to bug reports
- [ ] Update dependencies as needed
- [ ] Keep documentation current

---

## Quick Reference

### File Structure
```
custom_components/alexa/
├── __init__.py          (updated: coordinator setup)
├── const.py             (updated: Phase 2 constants)
├── models.py            (NEW: AlexaDevice, Capability)
├── api_client.py        (NEW: AlexaAPIClient, RateLimiter)
├── coordinator.py       (NEW: AlexaDeviceCoordinator)
├── switch.py            (NEW: AlexaSwitchEntity)
├── oauth.py             (Phase 1: unchanged)
└── config_flow.py       (Phase 1: unchanged)
```

### Key Patterns
1. `@dataclass` for models (simple, fast)
2. `DataUpdateCoordinator` for polling (built-in caching)
3. `CoordinatorEntity` for entities (automatic updates)
4. Token bucket for rate limiting (burst + sustained)
5. Exponential backoff for retries (1s, 2s, 4s)
6. Graceful degradation (mark unavailable, don't crash)

### Common Commands
```bash
# Run all tests
pytest tests/components/alexa/ -xvs --cov

# Run specific test file
pytest tests/components/alexa/test_coordinator.py -xvs

# Check coverage
pytest tests/components/alexa/ --cov --cov-report=html
open htmlcov/index.html

# Type checking
mypy custom_components/alexa/

# Linting
ruff check custom_components/alexa/
```

---

**Total Lines of Code**: ~800 lines production + ~600 lines tests
**Total Time**: 2-3 weeks (14 working days)
**Phase Completion**: When all checkboxes marked ✓
