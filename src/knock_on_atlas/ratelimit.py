import time


class RateLimiter:
    def __init__(self, per_second: float) -> None:
        self.interval = 1 / per_second
        self._next = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if now < self._next:
            time.sleep(self._next - now)
            now = self._next
        self._next = now + self.interval
