# Phase 2: Device Discovery & Switch Platform - Documentation Index

**Status**: READY FOR IMPLEMENTATION
**Timeline**: 2-3 weeks (14 working days)
**Goal**: Device discovery and switch control working end-to-end

---

## Quick Navigation

### 🚀 Getting Started (Read First)
1. **[Implementation Guide](PHASE2_IMPLEMENTATION_GUIDE.md)** - Comprehensive guide (60 min read)
   - Technical decisions with rationales
   - Complete code examples with annotations
   - Error handling patterns
   - Testing strategies
   - Common pitfalls to avoid

2. **[Implementation Checklist](PHASE2_CHECKLIST.md)** - Day-by-day task list (5 min read)
   - Step-by-step implementation order
   - Success criteria for each module
   - Testing requirements
   - Release preparation steps

3. **[Code Templates](PHASE2_CODE_TEMPLATES.md)** - Copy-paste ready code (20 min read)
   - Import organization standards
   - Retry logic (production-ready)
   - Rate limiter (token bucket)
   - API client skeleton
   - Coordinator template
   - Switch entity template
   - Test templates

4. **[Architecture Decision Record](../00_ARCHITECTURE/ADR-004-PHASE2-DEVICE-DISCOVERY.md)** - Design rationale (30 min read)
   - Why DataUpdateCoordinator over alternatives
   - Why @dataclass over Pydantic
   - Why token bucket rate limiting
   - Why exponential backoff retry
   - Performance targets and validation

---

## Phase 2 Overview

### What We're Building

**Goal**: Discover Alexa devices and create Home Assistant entities (switches initially)

**Components**:
1. **API Client** (`api_client.py`, ~250 lines)
   - HTTP client for Alexa Smart Home API
   - Rate limiting (token bucket: 20 burst, 10/s sustained)
   - Retry logic (exponential backoff: 1s, 2s, 4s)
   - Error handling (401 → reauth, 429 → retry, timeout → retry)

2. **Data Models** (`models.py`, ~150 lines)
   - `Capability`: Represents Alexa device capabilities
   - `AlexaDevice`: Represents Alexa device with state
   - Factory methods for parsing API responses
   - Type-safe capability checking

3. **Coordinator** (`coordinator.py`, ~150 lines)
   - `DataUpdateCoordinator` for polling Alexa API
   - Polls every 30 seconds (configurable)
   - Caches device data (dict for O(1) lookups)
   - Handles errors gracefully (mark entities unavailable)

4. **Switch Platform** (`switch.py`, ~150 lines)
   - `AlexaSwitchEntity` using `CoordinatorEntity`
   - turn_on/turn_off with immediate refresh
   - Device registry integration
   - Availability based on coordinator + device reachability

5. **Integration Updates** (`__init__.py`, ~100 lines additions)
   - Creates coordinator in `async_setup_entry`
   - Stores coordinator in `hass.data`
   - Forwards to switch platform

**Total**: ~800 lines production code + ~600 lines tests = ~1,400 lines

---

## Implementation Timeline

### Week 1: Foundation
- **Day 1**: Data models (`models.py`)
- **Days 2-3**: API client (`api_client.py`)
- **Day 4**: Testing and polish

### Week 2: Coordinator + Platform
- **Days 5-6**: Coordinator (`coordinator.py`)
- **Days 7-8**: Switch platform (`switch.py`)
- **Day 9**: Integration updates (`__init__.py`)

### Week 3: Polish + Testing
- **Days 10-11**: Integration testing (manual + automated)
- **Days 12-13**: Documentation + code review
- **Day 14**: Release preparation

---

## Key Technical Decisions

### 1. Async Pattern: All I/O Operations Are Async

```python
# ✅ CORRECT
async def fetch_devices(self) -> list[AlexaDevice]:
    async with self.session.get(url) as response:
        return await response.json()

# ❌ WRONG - Blocks event loop
def fetch_devices(self) -> list[AlexaDevice]:
    response = requests.get(url)  # BLOCKS!
    return response.json()
```

**Why**: Home Assistant is async-first. Blocking calls freeze the UI.

---

### 2. DataUpdateCoordinator: Single Polling Task

```python
# ✅ CORRECT - Single coordinator for all entities
class AlexaDeviceCoordinator(DataUpdateCoordinator):
    async def _async_update_data(self) -> dict[str, AlexaDevice]:
        devices = await self.api_client.fetch_devices()
        return {d.id: d for d in devices}

# ❌ WRONG - Each entity polls independently
class AlexaSwitchEntity(SwitchEntity):
    async def async_update(self) -> None:
        self._state = await self.api.get_state(self.device_id)
```

**Why**: Single API call for all devices (efficient), built-in caching, easy rate limiting.

---

### 3. Rate Limiting: Token Bucket Algorithm

```python
class RateLimiter:
    """Allows 20 burst requests, then limits to 10/s."""
    def __init__(self, capacity=20, refill_rate=10.0):
        self.tokens = float(capacity)
        self.refill_rate = refill_rate
```

**Why**: Allows fast startup (burst), prevents rate limits (sustained), simple implementation.

---

### 4. Retry Logic: Exponential Backoff

```python
# Retry 3 times with delays: 1s, 2s, 4s
for attempt in range(3):
    try:
        return await func()
    except RetryableError:
        delay = min(1.0 * (2.0 ** attempt), 60.0)
        await asyncio.sleep(delay)
```

**Why**: Industry standard (AWS, Google), prevents thundering herd, balances speed and kindness.

---

### 5. Error Handling: Graceful Degradation

```python
try:
    return await self.fetch_devices()
except AlexaAuthenticationError:
    # User must reauth
    raise ConfigEntryAuthFailed()
except AlexaRateLimitError:
    # Temporary, retry next cycle
    raise UpdateFailed("Rate limited")
```

**Why**: Auth errors need user action, temporary errors auto-recover, no crashes.

---

### 6. Data Structure: Dict for O(1) Lookups

```python
# ✅ CORRECT - O(1) lookup
coordinator.data = {device.id: device for device in devices}
device = coordinator.data[device_id]  # Instant

# ❌ WRONG - O(n) search
coordinator.data = devices  # list
device = next(d for d in devices if d.id == device_id)  # Slow
```

**Why**: Entities lookup device on every state access. O(1) vs O(n) = 50x faster for 50 devices.

---

### 7. Immediate Refresh: After State-Changing Commands

```python
async def async_turn_on(self, **kwargs: Any) -> None:
    await self.coordinator.async_set_device_power(self.device_id, True)
    # Coordinator calls async_request_refresh() immediately
```

**Why**: User expects immediate feedback (< 1s), not 0-30s polling delay.

---

## Code Quality Standards

### Type Hints: 100% Coverage

```python
# ✅ CORRECT
async def fetch_devices(self) -> list[AlexaDevice]:
    return await self._request()

# ❌ WRONG
async def fetch_devices(self):  # No return type
    return await self._request()
```

### Error Handling: Always Handle CancelledError

```python
# ✅ CORRECT
try:
    return await self.fetch()
except asyncio.CancelledError:
    raise  # Re-raise, don't catch
except Exception:
    raise UpdateFailed()

# ❌ WRONG
try:
    return await self.fetch()
except Exception:  # Catches CancelledError!
    raise UpdateFailed()
```

### Timeout: Always Use Timeout

```python
# ✅ CORRECT
async with asyncio.timeout(30):
    return await self.api.fetch()

# ❌ WRONG
return await self.api.fetch()  # Can hang forever
```

### Logging: Appropriate Levels

```python
_LOGGER.debug("Fetching devices...")  # Development details
_LOGGER.info("Configured with 50 devices")  # Normal operations
_LOGGER.warning("Rate limited, retrying...")  # Recoverable errors
_LOGGER.error("Failed after 3 retries")  # Errors preventing operation
_LOGGER.exception("Unexpected error")  # Unexpected with stack trace
```

---

## Testing Requirements

### Test Coverage: 90%+ Required

```bash
# Run tests with coverage
pytest tests/components/alexa/ --cov --cov-report=html

# View coverage report
open htmlcov/index.html
```

### Unit Tests: Fast, No I/O

```python
@pytest.fixture
def mock_api_client():
    """Mock API client (no real HTTP calls)."""
    client = AsyncMock()
    client.fetch_devices.return_value = [...]
    return client

async def test_coordinator_success(hass, mock_api_client):
    """Test coordinator with mocked API."""
    coordinator = AlexaDeviceCoordinator(hass, mock_api_client)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
```

### Integration Tests: With Home Assistant

```python
async def test_switch_turn_on(hass, coordinator):
    """Test switch entity end-to-end."""
    entity = AlexaSwitchEntity(coordinator, "device-1")
    await entity.async_turn_on()

    # Verify API called
    coordinator.api_client.set_power_state.assert_called_with("device-1", True)

    # Verify refresh requested
    coordinator.async_request_refresh.assert_called()
```

---

## Common Pitfalls

### 1. Blocking Calls in Async Context

```python
# ❌ WRONG
async def fetch(self):
    response = requests.get(url)  # Blocks!

# ✅ CORRECT
async def fetch(self):
    async with self.session.get(url) as response:
        return await response.json()
```

### 2. Mutable Default Arguments

```python
# ❌ WRONG
@dataclass
class Device:
    capabilities: list = []  # SHARED ACROSS INSTANCES!

# ✅ CORRECT
@dataclass
class Device:
    capabilities: list = field(default_factory=list)
```

### 3. Missing unique_id

```python
# ❌ WRONG
class MyEntity(Entity):
    def __init__(self, name):
        self.name = name  # Name can change!

# ✅ CORRECT
class MyEntity(Entity):
    def __init__(self, device_id):
        self._attr_unique_id = f"alexa_{device_id}"  # Stable
```

### 4. Not Re-raising Auth Errors

```python
# ❌ WRONG
async def update(self):
    try:
        return await self.fetch()
    except Exception:
        return {}  # Hides auth failure!

# ✅ CORRECT
async def update(self):
    try:
        return await self.fetch()
    except AuthError:
        raise ConfigEntryAuthFailed()  # Triggers reauth
```

---

## Success Criteria

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
- [ ] Reliability: No crashes, graceful error handling
- [ ] Maintainability: Clean code, well documented
- [ ] Testability: 90%+ coverage
- [ ] Security: No tokens logged, rate limiting works

### Code Quality
- [ ] Type hints: 100% coverage (mypy passes)
- [ ] Tests: 90%+ coverage
- [ ] Documentation: Complete and accurate
- [ ] No blocking calls in async code
- [ ] All public methods documented

---

## Quick Reference

### File Structure
```
custom_components/alexa/
├── __init__.py          (UPDATED: coordinator setup)
├── const.py             (UPDATED: Phase 2 constants)
├── models.py            (NEW: AlexaDevice, Capability)
├── api_client.py        (NEW: AlexaAPIClient, RateLimiter)
├── coordinator.py       (NEW: AlexaDeviceCoordinator)
├── switch.py            (NEW: AlexaSwitchEntity)
├── oauth.py             (Phase 1: unchanged)
└── config_flow.py       (Phase 1: unchanged)

tests/components/alexa/
├── test_models.py       (NEW: ~100 lines)
├── test_api_client.py   (NEW: ~150 lines)
├── test_coordinator.py  (NEW: ~150 lines)
├── test_switch.py       (NEW: ~150 lines)
└── test_init.py         (UPDATED: Phase 2 tests)
```

### Commands
```bash
# Run all tests
pytest tests/components/alexa/ -xvs --cov

# Run specific module tests
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

## Next Steps

1. **Read the Implementation Guide** (60 min)
   - [PHASE2_IMPLEMENTATION_GUIDE.md](PHASE2_IMPLEMENTATION_GUIDE.md)

2. **Review the Checklist** (5 min)
   - [PHASE2_CHECKLIST.md](PHASE2_CHECKLIST.md)

3. **Start Implementation** (Week 1, Day 1)
   - Begin with `models.py` (simplest, no dependencies)
   - Then `api_client.py` (depends on models)
   - Then `coordinator.py` (depends on API client)
   - Then `switch.py` (depends on coordinator)
   - Finally `__init__.py` updates (ties it all together)

4. **Test as You Go**
   - Write tests immediately after each module
   - Don't wait until end (hard to retrofit tests)
   - Aim for 90%+ coverage per module

---

## Questions?

- **Technical Questions**: Review [ADR-004](../00_ARCHITECTURE/ADR-004-PHASE2-DEVICE-DISCOVERY.md)
- **Code Examples**: Review [Code Templates](PHASE2_CODE_TEMPLATES.md)
- **Implementation Order**: Review [Checklist](PHASE2_CHECKLIST.md)
- **Design Rationale**: Review [Implementation Guide](PHASE2_IMPLEMENTATION_GUIDE.md)

---

**Status**: Documentation complete, ready for implementation
**Created**: 2025-11-13
**Author**: Development Team
**Next Review**: After Phase 2 completion
