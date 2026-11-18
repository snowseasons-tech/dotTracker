"""In-process sliding-window rate limiter.

dotTracker runs one API worker (the WebSocket fan-out is in-process), so one counter in memory is
shared by every request. With more than one worker, each worker would keep its own count and the
effective limit would multiply; that is another reason to keep one worker until a shared transport
is configured.
"""
import math
import threading
import time
from collections import OrderedDict, deque


class SlidingWindowLimiter:
    def __init__(self, limit, window_seconds, max_keys=50_000, clock=time.monotonic):
        if limit < 1 or window_seconds <= 0 or max_keys < 1:
            raise ValueError('limit, window_seconds and max_keys must be positive')
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._hits = OrderedDict()  # key -> recent hit times; least recently used first
        self._lock = threading.Lock()

    def allow(self, key):
        """Count one attempt for key. Return (allowed, retry_after_seconds).

        Refused attempts are not counted, so a client that waits out the retry time gets through.
        Memory is bounded: the least recently used keys are dropped beyond max_keys.
        """
        now = self._clock()
        with self._lock:
            hits = self._hits.pop(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                self._hits[key] = hits
                retry_after = math.ceil(hits[0] + self.window - now)
                return False, max(1, retry_after)
            hits.append(now)
            self._hits[key] = hits
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)
            return True, 0
