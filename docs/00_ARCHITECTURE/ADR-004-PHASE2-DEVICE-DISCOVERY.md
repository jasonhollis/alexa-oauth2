# ADR-004: Phase 2 Device Discovery Architecture

**Status**: PROPOSED
**Date**: 2025-11-13
**Deciders**: Development Team
**Context**: Phase 2 implementation - Device discovery and control platform

---

## Context and Problem Statement

Phase 1 delivered OAuth2 authentication. Phase 2 must implement device discovery and control, requiring decisions on:

1. How to poll Alexa API efficiently without rate limits
2. How to represent devices and capabilities in Python
3. How to coordinate data updates across entities
4. How to handle errors gracefully (temporary vs permanent failures)
5. How to ensure responsive UI (immediate updates after commands)

**Constraints**:
- Alexa API has rate limits (exact limits unknown, conservatively assume 10 req/s)
- Network requests may fail temporarily (timeouts, 429 errors)
- Authentication tokens may expire or be revoked (must trigger reauth)
- Home Assistant must remain responsive (no blocking calls)
- Devices can be online/offline/unreachable (must reflect in entity availability)

---

## Decision Drivers

1. **Performance**: Updates must be fast (< 5s for 50 devices)
2. **Reliability**: Graceful degradation, no crashes
3. **User Experience**: Immediate feedback after commands, clear availability status
4. **Maintainability**: Clean code following Home Assistant patterns
5. **Testability**: Comprehensive tests, easy to mock
6. **Scalability**: Support users with 1-100+ devices

---

## Considered Options

### Option 1: Direct API Calls from Entities (Rejected)

**Pattern**: Each entity calls Alexa API directly when `is_on` accessed

```python
class AlexaSwitchEntity(SwitchEntity):
    @property
    def is_on(self) -> bool:
        # BAD: Blocks UI, no caching
        return asyncio.run(self.api.get_state(self.device_id))
```

**Pros**:
- Simple implementation
- Always fresh data

**Cons**:
- Blocks Home Assistant UI (property access must be sync)
- No caching (hammers API with requests)
- No rate limiting (guaranteed 429 errors)
- No coordination between entities
- Poor performance (network call per property access)

**Decision**: ❌ REJECTED (violates HA patterns, poor performance)

---

### Option 2: Manual Polling with asyncio.create_task (Rejected)

**Pattern**: Each entity manages own polling task

```python
class AlexaSwitchEntity(SwitchEntity):
    async def async_added_to_hass(self) -> None:
        self._polling_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while True:
            self._state = await self.api.get_state(self.device_id)
            await asyncio.sleep(30)
```

**Pros**:
- Async (doesn't block UI)
- Fresh data every 30s

**Cons**:
- N polling tasks for N entities (inefficient)
- No coordination (all entities poll at different times)
- Hard to implement rate limiting across entities
- No built-in error handling
- Difficult to test (async tasks, timing dependencies)
- Reinvents Home Assistant's DataUpdateCoordinator

**Decision**: ❌ REJECTED (reinvents wheel, poor coordination)

---

### Option 3: DataUpdateCoordinator with Token Bucket Rate Limiting (CHOSEN)

**Pattern**: Single coordinator polls API, all entities subscribe to updates

```python
class AlexaDeviceCoordinator(DataUpdateCoordinator[dict[str, AlexaDevice]]):
    async def _async_update_data(self) -> dict[str, AlexaDevice]:
        # Single API call for all devices
        devices = await self.api_client.fetch_devices()
        return {d.id: d for d in devices}

class AlexaSwitchEntity(CoordinatorEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        # Fast: No network call, just dict lookup
        return self.coordinator.data[self.device_id].is_on
```

**Pros**:
- Single polling task for all entities (efficient)
- Built-in caching (fast property access)
- Built-in error handling (UpdateFailed, ConfigEntryAuthFailed)
- Easy to implement rate limiting (one place)
- Follows Home Assistant best practices
- Easy to test (coordinator is isolated, mockable)
- Automatic entity updates when data changes

**Cons**:
- All devices updated together (but this is actually good for rate limiting)
- Slight delay for entity availability (30s poll cycle)

**Decision**: ✅ CHOSEN (best balance, follows HA patterns)

---

## Technical Decisions

### 1. Data Structures: @dataclass vs Pydantic vs TypedDict

**Decision**: Use `@dataclass` for domain models

**Options Evaluated**:

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| `@dataclass` | Built-in, fast, simple, good defaults | Manual validation | ✅ CHOSEN |
| Pydantic | Validation, serialization | External dep, slower, overkill for DTOs | ❌ |
| `TypedDict` | Static typing | No validation, no methods, no defaults | ❌ |
| Plain classes | Maximum control | Verbose, no free features | ❌ |

**Rationale**:
- `@dataclass` is Python standard library (no dependencies)
- Fast (faster than Pydantic for simple DTOs)
- Sufficient validation via `from_api_response()` factory methods
- Home Assistant prefers stdlib over external dependencies
- Easy to add methods and properties

**Implementation**:
```python
@dataclass(frozen=True)  # Immutable for Capability
class Capability:
    type: str
    interface: str
    version: str = "3"
    properties: dict[str, Any] = field(default_factory=dict)

@dataclass  # Mutable for AlexaDevice (state changes)
class AlexaDevice:
    id: str
    friendly_name: str
    capabilities: list[Capability]
    is_on: bool = False  # State can change
```

---

### 2. Coordinator Data Type: List vs Dict

**Decision**: Use `dict[str, AlexaDevice]` keyed by device ID

**Options Evaluated**:

| Option | Lookup Time | Memory | Entity Access Pattern | Decision |
|--------|-------------|--------|----------------------|----------|
| `list[AlexaDevice]` | O(n) | Lower | `next(d for d in data if d.id == id)` | ❌ |
| `dict[str, AlexaDevice]` | O(1) | Higher | `data[device_id]` | ✅ CHOSEN |

**Rationale**:
- Entities need to lookup their device on every state access
- O(1) dict lookup vs O(n) list search
- For 50 devices: 50 lookups vs 2500 comparisons per update cycle
- Memory overhead negligible (50 devices ≈ 10KB)

**Implementation**:
```python
class AlexaDeviceCoordinator(DataUpdateCoordinator[dict[str, AlexaDevice]]):
    async def _async_update_data(self) -> dict[str, AlexaDevice]:
        devices = await self.api_client.fetch_devices()
        return {device.id: device for device in devices}

class AlexaSwitchEntity(CoordinatorEntity):
    @property
    def device(self) -> AlexaDevice:
        # O(1) lookup
        return self.coordinator.data[self._device_id]
```

---

### 3. Rate Limiting: Token Bucket vs Sliding Window vs Leaky Bucket

**Decision**: Token bucket algorithm

**Options Evaluated**:

| Algorithm | Allows Burst | Complexity | Memory | Decision |
|-----------|--------------|------------|--------|----------|
| Token Bucket | Yes | Low | Low | ✅ CHOSEN |
| Sliding Window | Limited | Medium | Medium | ❌ |
| Leaky Bucket | No | Low | Low | ❌ |
| Fixed Window | Yes | Low | Low | ❌ (thundering herd) |

**Rationale**:
- Token bucket allows burst traffic (20 requests immediately)
- Then limits to sustained rate (10 req/s)
- Good for startup (fetch all devices quickly)
- Good for normal operation (respect rate limits)
- Simple to implement (no external dependencies)
- Used by AWS SDK, Google SDK, etc.

**Implementation**:
```python
class RateLimiter:
    """Token bucket: capacity=20, refill_rate=10/s"""

    def __init__(self, capacity: int = 20, refill_rate: float = 10.0):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_update = datetime.now()

    async def acquire(self, tokens: int = 1) -> None:
        """Block until tokens available."""
        while self.tokens < tokens:
            self._refill()
            needed = tokens - self.tokens
            wait_time = needed / self.refill_rate
            await asyncio.sleep(wait_time)

        self.tokens -= tokens
```

**Parameters**:
- Capacity: 20 (conservative, allows initial burst)
- Refill rate: 10/s (conservative, Alexa limits unknown)
- Adjustable via config if needed

---

### 4. Retry Strategy: Exponential Backoff vs Fixed Delay vs No Retry

**Decision**: Exponential backoff with jitter (1s, 2s, 4s)

**Options Evaluated**:

| Strategy | Pros | Cons | Decision |
|----------|------|------|----------|
| Exponential backoff | Avoids thundering herd, industry standard | Slightly complex | ✅ CHOSEN |
| Fixed delay | Simple | Thundering herd possible | ❌ |
| No retry | Very simple | Fails on transient errors | ❌ |
| Linear backoff | Simple | Slow recovery | ❌ |

**Rationale**:
- Exponential backoff is industry standard (AWS, Google, Microsoft SDKs)
- Prevents thundering herd (all clients retrying at same time)
- Jitter adds randomness (0.5x - 1.5x) to prevent synchronization
- 3 attempts: Initial, +1s, +2s, +4s = 7s total
- Balances responsiveness and API kindness

**Implementation**:
```python
for attempt in range(MAX_RETRY_ATTEMPTS):
    try:
        return await func(*args, **kwargs)
    except RetryableError as err:
        if attempt == MAX_RETRY_ATTEMPTS - 1:
            raise

        delay = min(
            INITIAL_DELAY * (MULTIPLIER ** attempt),
            MAX_DELAY
        )

        # Add jitter to prevent thundering herd
        delay *= (0.5 + random.random())  # 50-150% of delay

        await asyncio.sleep(delay)
```

**Parameters**:
- Max attempts: 3
- Initial delay: 1.0s
- Multiplier: 2.0
- Max delay: 60s
- Jitter: 50-150%

---

### 5. Error Handling: Crash vs Graceful Degradation vs Ignore

**Decision**: Graceful degradation with error classification

**Error Classification**:

| Error Type | Exception | Action | Entity State | Retry |
|------------|-----------|--------|--------------|-------|
| Authentication (401) | `ConfigEntryAuthFailed` | Trigger reauth | Unavailable | No |
| Rate limit (429) | `UpdateFailed` | Mark unavailable | Unavailable | Yes (next cycle) |
| Timeout | `UpdateFailed` | Mark unavailable | Unavailable | Yes (next cycle) |
| Network error | `UpdateFailed` | Mark unavailable | Unavailable | Yes (next cycle) |
| Device unreachable | (normal) | Mark entity unavailable | Unavailable | No |
| Unknown error | `UpdateFailed` | Log + mark unavailable | Unavailable | Yes (next cycle) |

**Rationale**:
- Authentication errors need user action (reauth)
- Temporary errors (rate limit, timeout) auto-recover
- Device-level errors don't affect other devices
- UI shows clear "unavailable" badge
- No crashes, no data loss

**Implementation**:
```python
async def _async_update_data(self) -> dict[str, AlexaDevice]:
    try:
        return await self._fetch_devices()

    except AlexaAuthenticationError:
        # Permanent: User must reauth
        raise ConfigEntryAuthFailed("Authentication failed")

    except AlexaRateLimitError as err:
        # Temporary: Will retry next cycle
        raise UpdateFailed(f"Rate limited: {err}")

    except asyncio.TimeoutError:
        # Temporary: Will retry next cycle
        raise UpdateFailed("Timeout")

    except asyncio.CancelledError:
        # Expected during shutdown
        raise

    except Exception as err:
        # Unexpected: Log but don't crash
        _LOGGER.exception("Unexpected error: %s", err)
        raise UpdateFailed(f"Error: {err}")
```

---

### 6. Update Interval: 10s vs 30s vs 60s vs 120s

**Decision**: 30 seconds (configurable)

**Options Evaluated**:

| Interval | API Load | Responsiveness | Battery (mobile) | Decision |
|----------|----------|----------------|------------------|----------|
| 10s | High | Excellent | Poor | ❌ |
| 30s | Medium | Good | Good | ✅ CHOSEN |
| 60s | Low | Fair | Excellent | ❌ |
| 120s | Very low | Poor | Excellent | ❌ |

**Rationale**:
- 30s balances responsiveness and API load
- Most IoT platforms use 30-60s polling
- State changes via commands trigger immediate refresh (responsive UX)
- Can be adjusted per-user if needed
- Alexa devices typically don't change state rapidly

**Implementation**:
```python
coordinator = AlexaDeviceCoordinator(
    hass=hass,
    api_client=api_client,
    update_interval=timedelta(seconds=30),  # Configurable
)
```

**Future Enhancement**: WebSocket for real-time updates (if Alexa adds support)

---

### 7. Immediate Refresh: After Command vs Rely on Polling

**Decision**: Immediate refresh after state-changing commands

**Rationale**:
- User expects immediate feedback after pressing switch
- Without immediate refresh: 0-30s delay (poor UX)
- With immediate refresh: < 1s delay (good UX)
- Trade-off: One extra API call per command (acceptable)

**Implementation**:
```python
async def async_turn_on(self, **kwargs: Any) -> None:
    """Turn switch on."""
    await self.coordinator.async_set_device_power(self._device_id, True)
    # Coordinator immediately calls async_request_refresh()

async def async_set_device_power(self, device_id: str, power_on: bool) -> None:
    """Set power and refresh immediately."""
    await self.api_client.set_power_state(device_id, power_on)
    await self.async_request_refresh()  # Immediate update
```

---

## Consequences

### Positive

1. **Performance**: O(1) entity lookups, single API call per update
2. **Reliability**: Graceful error handling, no crashes
3. **Maintainability**: Follows HA patterns, easy to understand
4. **Testability**: Coordinator isolated, easy to mock
5. **Scalability**: Supports 1-100+ devices efficiently
6. **User Experience**: Immediate feedback, clear availability status

### Negative

1. **Memory**: Dict uses slightly more memory than list (negligible)
2. **Complexity**: Coordinator adds abstraction layer (but it's standard HA pattern)
3. **API Load**: Immediate refresh after commands adds one extra call (acceptable)

### Risks

1. **Rate Limiting**: If limits are lower than assumed (10 req/s), may hit 429 errors
   - Mitigation: Conservative limits (20 burst, 10/s), exponential backoff
2. **Large Device Count**: 100+ devices may slow updates
   - Mitigation: Single API call fetches all devices (efficient)
3. **Network Latency**: Slow networks may cause timeouts
   - Mitigation: 30s timeout, retry logic

---

## Implementation Summary

### Architecture

```
┌─────────────────────────────────────────────┐
│           Home Assistant Core               │
│  ┌───────────────────────────────────────┐  │
│  │       __init__.py (Entry)             │  │
│  │  • Creates AlexaAPIClient             │  │
│  │  • Creates AlexaDeviceCoordinator     │  │
│  │  • Forwards to platforms              │  │
│  └────────────┬──────────────────────────┘  │
│               │                              │
│  ┌────────────▼──────────────────────────┐  │
│  │  AlexaDeviceCoordinator               │  │
│  │  • Polls every 30s                    │  │
│  │  • Caches in dict[str, AlexaDevice]   │  │
│  │  • Notifies entities on change        │  │
│  └────────────┬──────────────────────────┘  │
│               │                              │
│      ┌────────┴─────────┐                   │
│      │                  │                   │
│  ┌───▼───┐          ┌───▼───┐              │
│  │Switch │          │Light  │              │
│  │Entity │          │Entity │              │
│  └───────┘          └───────┘              │
└─────────────────────────────────────────────┘
         │
         │ HTTP (rate limited, retried)
         │
    ┌────▼────────────┐
    │ Alexa API       │
    │ api.amazon.com  │
    └─────────────────┘
```

### Key Components

1. **AlexaAPIClient**: HTTP client with rate limiting and retry
2. **AlexaDeviceCoordinator**: Polling coordinator with error handling
3. **AlexaSwitchEntity**: Switch platform using CoordinatorEntity
4. **AlexaDevice**: Data model (dataclass)
5. **RateLimiter**: Token bucket rate limiter

### Code Metrics

- **Production Code**: ~800 lines
  - models.py: ~150 lines
  - api_client.py: ~250 lines
  - coordinator.py: ~150 lines
  - switch.py: ~150 lines
  - __init__.py updates: ~100 lines
- **Test Code**: ~600 lines
- **Total**: ~1,400 lines

---

## Validation

### Success Criteria

- [ ] Devices discovered from Alexa API
- [ ] Switch entities created and functional
- [ ] turn_on/turn_off work with immediate feedback
- [ ] Entities marked unavailable on errors
- [ ] Reauth triggered on 401 errors
- [ ] No 429 rate limit errors in normal operation
- [ ] Performance: < 5s to fetch 50 devices
- [ ] Test coverage: 90%+

### Performance Targets

| Metric | Target | Rationale |
|--------|--------|-----------|
| Device fetch (10 devices) | < 2s | Good UX |
| Device fetch (50 devices) | < 5s | Acceptable UX |
| Command latency | < 1s | Responsive UX |
| Polling interval | 30s | Balance load/freshness |
| Memory per device | < 1KB | Scalable |
| API calls per minute | < 60 | Avoid rate limits |

---

## Alternatives Considered

### Alternative: Event-Driven Updates (WebSocket)

**Pattern**: Subscribe to Alexa events via WebSocket

**Pros**:
- Real-time updates (no polling delay)
- Lower API load (no periodic polls)
- Better battery life (push vs pull)

**Cons**:
- Alexa doesn't currently offer WebSocket API
- Would require significant Alexa API changes
- Complexity (connection management, reconnection)

**Decision**: Not feasible (Alexa API limitation)
**Future**: If Alexa adds WebSocket support, migrate to event-driven

---

### Alternative: On-Demand Fetching (No Polling)

**Pattern**: Only fetch device state when user views entity

**Pros**:
- Zero API load when UI closed
- Minimal rate limit concerns

**Cons**:
- Stale state when UI opened (confusing)
- Slow UI (wait for API on every view)
- Automations see stale state
- Poor UX (delays everywhere)

**Decision**: Rejected (poor UX, violates HA patterns)

---

## References

- [Home Assistant DataUpdateCoordinator](https://developers.home-assistant.io/docs/integration_fetching_data)
- [Home Assistant Entity Availability](https://developers.home-assistant.io/docs/core/entity/#available)
- [Token Bucket Algorithm](https://en.wikipedia.org/wiki/Token_bucket)
- [Exponential Backoff (Google Cloud)](https://cloud.google.com/iot/docs/how-tos/exponential-backoff)
- [AWS SDK Retry Strategy](https://docs.aws.amazon.com/general/latest/gr/api-retries.html)

---

**Decision Date**: 2025-11-13
**Status**: PROPOSED (awaiting implementation)
**Next Review**: After Phase 2 completion
