from app.auth.ratelimit import LoginRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_blocks_after_max_failures_and_expires() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_failures_per_user=3, window_seconds=60, clock=clock)
    for _ in range(3):
        assert limiter.retry_after("1.2.3.4", "alice") == 0
        limiter.register_failure("1.2.3.4", "alice")
    assert limiter.retry_after("1.2.3.4", "alice") == 60
    assert limiter.retry_after("1.2.3.4", "ALICE ") == 60  # normalised
    assert limiter.retry_after("5.6.7.8", "alice") == 0  # other client unaffected
    clock.now += 61
    assert limiter.retry_after("1.2.3.4", "alice") == 0


def test_ip_limit_across_usernames() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_failures_per_user=100, max_failures_per_ip=4, clock=clock)
    for i in range(4):
        limiter.register_failure("1.2.3.4", f"user{i}")
    assert limiter.retry_after("1.2.3.4", "someone-else") > 0


def test_success_resets_user_counter() -> None:
    limiter = LoginRateLimiter(max_failures_per_user=2)
    limiter.register_failure("ip", "alice")
    limiter.register_success("ip", "alice")
    limiter.register_failure("ip", "alice")
    assert limiter.retry_after("ip", "alice") == 0
