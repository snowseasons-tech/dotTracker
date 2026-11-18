"""Sliding-window limiter rules (no database needed)."""
import pytest

from app.rate_limit import SlidingWindowLimiter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_allows_up_to_the_limit_then_refuses_with_a_retry_time():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(3, 60, clock=clock)
    assert [limiter.allow('tracker')[0] for _ in range(3)] == [True, True, True]
    assert limiter.allow('tracker') == (False, 60)


def test_window_slides_and_refused_attempts_are_not_counted():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(2, 60, clock=clock)
    assert limiter.allow('tracker')[0]
    clock.now = 30
    assert limiter.allow('tracker')[0]
    clock.now = 45
    assert limiter.allow('tracker') == (False, 15)
    clock.now = 45.5
    assert limiter.allow('tracker') == (False, 15)  # the refused attempts above were not recorded
    clock.now = 60
    assert limiter.allow('tracker')[0]  # the hit at t=0 has left the window


def test_keys_are_independent_and_memory_is_bounded_by_least_recent_use():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(1, 60, max_keys=2, clock=clock)
    assert limiter.allow('a')[0] and limiter.allow('b')[0]
    assert limiter.allow('c')[0]          # evicts 'a', the least recently used key
    assert limiter.allow('a')[0]          # 'a' was forgotten, so it is allowed again
    assert not limiter.allow('c')[0]      # 'c' is still counted


def test_rejects_nonsense_settings():
    with pytest.raises(ValueError):
        SlidingWindowLimiter(0, 60)
    with pytest.raises(ValueError):
        SlidingWindowLimiter(10, 0)
