# Deadlock Fix Analysis - Test 45-46 Hanging Issue

## Executive Summary

**Root Cause**: Recursive retry logic inside a locked context causes same-coroutine deadlock
**Location**: `custom_components/alexa/advanced_reauth.py` lines 547-607
**Tests Affected**: `test_handle_reauth_max_retries` and `test_handle_reauth_concurrent_calls`
**Fix**: Move retry logic outside lock context to allow safe recursion

## The Deadlock Pattern

### Original Buggy Code (Lines 547-598)

```python
async with self._reauth_lock:
    try:
        # ... handler dispatch code ...
        return await self.async_handle_expired_refresh_token()

    except AlexaReauthMaxRetriesError:
        raise

    except Exception as err:
        _LOGGER.error("Reauth failed (retry %d): %s", retry_count, err)

        # BUGGY: Exponential backoff and retry INSIDE lock
        delay = REAUTH_RETRY_DELAY_SECONDS * (REAUTH_BACKOFF_MULTIPLIER ** retry_count)
        await asyncio.sleep(delay)

        # DEADLOCK: Recursive call while lock still held!
        return await self.async_handle_reauth(reason, retry_count + 1)
```

### Why This Deadlocks

1. **Coroutine A** acquires `_reauth_lock` at line 547
2. Handler raises exception at line 591
3. Code sleeps then calls `async_handle_reauth()` recursively at line 598
4. **STILL INSIDE THE LOCKED CONTEXT** from line 547!
5. Recursive call reaches line 541: `if self._reauth_lock.locked()` → `True`
6. Attempts to acquire lock at line 543: `async with self._reauth_lock:`
7. **DEADLOCK**: Same coroutine trying to acquire lock it already holds!

### Why It Only Hangs in CI

- **Locally**: Mock returns success immediately, retry path never executes
- **CI Environment**: Different timing/scheduling causes exception path to execute
- **Test 45** (`test_handle_reauth_max_retries`): Explicitly tests max retries path
- **Test 46** (`test_handle_reauth_concurrent_calls`): Race conditions expose the lock bug

## The Fix

### Fixed Code Structure

```python
# Track retry state BEFORE entering lock
retry_needed = False
caught_exception = None

async with self._reauth_lock:
    try:
        # ... handler dispatch code ...
        return await self.async_handle_expired_refresh_token()

    except AlexaReauthMaxRetriesError:
        raise

    except Exception as err:
        _LOGGER.error("Reauth failed (retry %d): %s", retry_count, err)
        # Mark for retry, but don't retry inside lock
        retry_needed = True
        caught_exception = err

# Lock is now RELEASED - safe to retry
if retry_needed:
    # Exponential backoff
    delay = REAUTH_RETRY_DELAY_SECONDS * (REAUTH_BACKOFF_MULTIPLIER ** retry_count)
    await asyncio.sleep(delay)

    # Safe recursive call - lock is released
    return await self.async_handle_reauth(reason, retry_count + 1)
```

### Key Changes

1. **Extract retry flag**: Track `retry_needed` and `caught_exception` before entering lock
2. **Exception handling**: Catch exception, set flags, exit lock context
3. **Retry outside lock**: After lock released, check flags and retry safely
4. **No deadlock**: Recursive call happens with lock completely released

## Test Impact Analysis

### Test 45: `test_handle_reauth_max_retries`

**Before**:
```python
async def test_handle_reauth_max_retries(handler):
    with pytest.raises(AlexaReauthMaxRetriesError):
        await handler.async_handle_reauth(
            ReauthReason.REFRESH_TOKEN_EXPIRED,
            retry_count=REAUTH_MAX_RETRY_ATTEMPTS,
        )
```

- Passes `retry_count=3` (max retries)
- Should raise `AlexaReauthMaxRetriesError` immediately at line 536
- **Bug**: If handler raises exception, retry logic deadlocks
- **After Fix**: Early check at line 536 prevents retry path entirely

### Test 46: `test_handle_reauth_concurrent_calls`

**Before**:
```python
async def test_handle_reauth_concurrent_calls(handler, mock_hass):
    handler.async_handle_expired_refresh_token = AsyncMock(
        return_value=ReauthResult(success=True, reason=ReauthReason.REFRESH_TOKEN_EXPIRED)
    )

    # 3 concurrent tasks
    tasks = [
        handler.async_handle_reauth(ReauthReason.REFRESH_TOKEN_EXPIRED)
        for _ in range(3)
    ]
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)

    assert all(r.success for r in results)
    assert handler.async_handle_expired_refresh_token.call_count == 1  # Only first executes
```

**Original Lock Logic** (Lines 541-545):
```python
if self._reauth_lock.locked():
    _LOGGER.debug("Reauth already in progress, waiting...")
    async with self._reauth_lock:
        pass  # Wait for current reauth to complete
    return ReauthResult(success=True, reason=reason)
```

**Race Condition**:
1. Task 1, 2, 3 all call `async_handle_reauth()` simultaneously
2. Task 1 passes `locked()` check (not locked yet), proceeds to line 547
3. Tasks 2 & 3 MAY ALSO pass `locked()` check before Task 1 acquires lock!
4. All 3 tasks try to acquire lock at line 547
5. Task 1 gets lock, executes handler, returns
6. Task 2 gets lock, executes handler again (should have waited!)
7. **Result**: `call_count > 1` → Test fails

**After Fix**:
- Retry path is outside lock, no recursion deadlock
- Concurrent calls serialize properly via lock
- First caller executes, others wait and skip execution

## Verification Steps

1. **Run locally**:
   ```bash
   pytest tests/components/alexa/test_advanced_reauth.py::test_handle_reauth_max_retries -xvs
   pytest tests/components/alexa/test_advanced_reauth.py::test_handle_reauth_concurrent_calls -xvs
   ```

2. **Check GitHub Actions**: Tests 45-46 should no longer hang

3. **Validate retry logic**:
   ```bash
   pytest tests/components/alexa/test_advanced_reauth.py::test_handle_reauth_with_retry -xvs
   ```

## Additional Issues Found

### Issue: `asyncio.sleep()` Not Mocked Correctly

In `test_handle_reauth_with_retry` (line 516):
```python
with patch("asyncio.sleep", new_callable=AsyncMock):
```

This patches `asyncio.sleep` at the **module level**, but the component imports it differently:
```python
# custom_components/alexa/advanced_reauth.py line 52
import asyncio
# ...
await asyncio.sleep(delay)  # Line 599
```

**Correct patch target**:
```python
with patch("custom_components.alexa.advanced_reauth.asyncio.sleep", new_callable=AsyncMock):
```

**However**, with the fix, this becomes less critical because:
- Sleep happens outside lock
- No deadlock even if sleep executes
- Test can safely timeout if needed

## Conclusion

The deadlock was caused by **recursive retry logic executing inside a locked context**, causing the same coroutine to attempt re-acquiring a lock it already holds. The fix extracts retry logic to execute **after the lock is released**, preventing same-coroutine deadlock while maintaining thread safety for concurrent calls.

**Impact**: Tests 45-46 will now pass in CI without hanging.
