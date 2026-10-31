"""Request limits shared by the Streamlit and CLI entry points."""
from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


MAX_PITCH_CHARS = _bounded_int("MAX_PITCH_CHARS", 4000, 1, 10000)
PITCH_RATE_LIMIT_REQUESTS = _bounded_int("PITCH_RATE_LIMIT_REQUESTS", 3, 1, 20)
PITCH_RATE_LIMIT_WINDOW_SECONDS = _bounded_int(
    "PITCH_RATE_LIMIT_WINDOW_SECONDS", 600, 1, 3600
)
MAX_CONCURRENT_PANEL_RUNS = _bounded_int("MAX_CONCURRENT_PANEL_RUNS", 1, 1, 4)
PANEL_PROVIDER_REQUESTS = 7  # six shark verdicts and one host summary
MAX_PANEL_DURATION_SECONDS = _bounded_int(
    "MAX_PANEL_DURATION_SECONDS", 240, 30, 600)
APP_GROQ_REQUESTS_PER_MINUTE = _bounded_int(
    "APP_GROQ_REQUESTS_PER_MINUTE", 10, PANEL_PROVIDER_REQUESTS, 10)
APP_GROQ_REQUESTS_PER_DAY = _bounded_int(
    "APP_GROQ_REQUESTS_PER_DAY", 500, PANEL_PROVIDER_REQUESTS, 500)
GROQ_REQUEST_TIMEOUT_SECONDS = _bounded_int(
    "GROQ_REQUEST_TIMEOUT_SECONDS", 45, 1, 120)
# Keep actual provider calls within the reserved panel budget.
GROQ_MAX_RETRIES = 0
GROQ_MAX_OUTPUT_TOKENS = _bounded_int(
    "GROQ_MAX_OUTPUT_TOKENS", 5000, 128, 5000)
MAX_PANEL_COMPLETION_TOKENS = PANEL_PROVIDER_REQUESTS * GROQ_MAX_OUTPUT_TOKENS
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


class PitchTooLongError(ValueError):
    """Raised when a pitch exceeds the configured model-input bound."""


class PitchFileError(ValueError):
    """Raised when a pitch file cannot be read as UTF-8 text."""


class ProviderConfigurationError(RuntimeError):
    """Raised when required Groq configuration is missing or invalid."""


class RateLimitExceededError(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Request limit reached")


class PanelBusyError(Exception):
    """Raised rather than queueing unbounded work when all panel slots are busy."""


class ProviderRequestError(RuntimeError):
    """Safe user-facing failure for provider requests."""


class ProviderBudgetExceededError(ProviderRequestError):
    """Raised when a panel would exceed the app's local Groq request budget."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            "The demo has reached its Groq request limit. "
            f"Please try again in about {retry_after_seconds} seconds."
        )


class PanelDeadlineExceededError(ProviderRequestError):
    """Raised when the full panel exceeds its wall-clock budget."""

    def __init__(self):
        super().__init__(
            "The panel exceeded its time limit. Please try again with a shorter pitch."
        )


class ModelOutputError(RuntimeError):
    """Raised when model output does not satisfy the verdict contract."""


def validate_provider_config() -> None:
    if not os.getenv("GROQ_API_KEY", "").strip():
        raise ProviderConfigurationError(
            "GROQ_API_KEY is not configured. Set it in the environment or local .env file."
        )
    if not GROQ_MODEL.strip():
        raise ProviderConfigurationError("GROQ_MODEL must not be empty.")


def validate_pitch(pitch: str) -> None:
    if not isinstance(pitch, str):
        raise TypeError("pitch must be text")
    if not pitch.strip():
        raise ValueError("Enter a pitch before submitting it.")
    if len(pitch) > MAX_PITCH_CHARS:
        raise PitchTooLongError(
            f"Pitch is too long ({len(pitch)} characters). "
            f"The limit is {MAX_PITCH_CHARS} characters."
        )


class SlidingWindowRateLimiter:
    """Process-local limiter; use a trusted gateway/shared store across replicas."""

    def __init__(self, max_requests: int, window_seconds: int):
        if max_requests < 1 or window_seconds < 1:
            raise ValueError("rate limit settings must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client_key: str, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        cutoff = current - self.window_seconds
        with self._lock:
            for key in list(self._requests):
                bucket = self._requests[key]
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
                if not bucket:
                    del self._requests[key]

            bucket = self._requests.setdefault(client_key, deque())
            if len(bucket) >= self.max_requests:
                retry_after = max(1, math.ceil(
                    bucket[0] + self.window_seconds - current))
                raise RateLimitExceededError(retry_after)
            bucket.append(current)


class SlidingWindowRequestBudget:
    """Atomically reserve capacity across multiple rolling request windows."""

    def __init__(self, windows: list[tuple[int, int]]):
        if not windows or any(limit < 1 or seconds < 1
                              for limit, seconds in windows):
            raise ValueError("request budget windows must be positive")
        self._windows = [
            (limit, seconds, deque()) for limit, seconds in windows
        ]
        self._lock = threading.Lock()

    def reserve(self, request_count: int, now: float | None = None) -> None:
        if request_count < 1:
            raise ValueError("request_count must be positive")
        if any(request_count > limit for limit, _seconds, _requests in self._windows):
            raise ValueError(
                "request_count exceeds a window's configured capacity")
        current = time.monotonic() if now is None else now
        with self._lock:
            retry_after = 0
            for limit, seconds, requests in self._windows:
                cutoff = current - seconds
                while requests and requests[0] <= cutoff:
                    requests.popleft()
                if len(requests) + request_count > limit:
                    required_expirations = len(
                        requests) + request_count - limit
                    retry_after = max(
                        retry_after,
                        math.ceil(requests[required_expirations - 1]
                                  + seconds - current),
                    )
            if retry_after:
                raise RateLimitExceededError(max(1, retry_after))

            for _limit, _seconds, requests in self._windows:
                requests.extend([current] * request_count)


_rate_limiter = SlidingWindowRateLimiter(
    PITCH_RATE_LIMIT_REQUESTS, PITCH_RATE_LIMIT_WINDOW_SECONDS
)
_panel_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PANEL_RUNS)


def check_pitch_rate_limit(client_key: str) -> None:
    _rate_limiter.check(client_key)


# This is an in-process preflight for the account's provider limits. Groq's
# account-level quotas remain the authoritative shared limit across restarts
# and app replicas.
_provider_request_budget = SlidingWindowRequestBudget([
    (APP_GROQ_REQUESTS_PER_MINUTE, 60),
    (APP_GROQ_REQUESTS_PER_DAY, 24 * 60 * 60),
])
_panel_deadline: ContextVar[float | None] = ContextVar(
    "panel_deadline", default=None)


def check_provider_panel_budget() -> None:
    try:
        _provider_request_budget.reserve(PANEL_PROVIDER_REQUESTS)
    except RateLimitExceededError as exc:
        raise ProviderBudgetExceededError(exc.retry_after_seconds) from None


@contextmanager
def panel_time_budget(duration_seconds: float | None = None):
    duration = (MAX_PANEL_DURATION_SECONDS if duration_seconds is None
                else duration_seconds)
    if duration <= 0:
        raise ValueError("panel time budget must be positive")
    token = _panel_deadline.set(time.monotonic() + duration)
    try:
        yield
    finally:
        _panel_deadline.reset(token)


def groq_request_timeout_seconds() -> float:
    deadline = _panel_deadline.get()
    if deadline is None:
        return GROQ_REQUEST_TIMEOUT_SECONDS
    remaining = deadline - time.monotonic()
    if remaining <= 0.1:
        raise PanelDeadlineExceededError()
    return min(float(GROQ_REQUEST_TIMEOUT_SECONDS), remaining)


def ensure_panel_time_remaining() -> None:
    deadline = _panel_deadline.get()
    if deadline is not None and deadline - time.monotonic() <= 0:
        raise PanelDeadlineExceededError()


@contextmanager
def panel_execution_slot():
    if not _panel_slots.acquire(blocking=False):
        raise PanelBusyError("The panel is busy. Please try again shortly.")
    try:
        yield
    finally:
        _panel_slots.release()
