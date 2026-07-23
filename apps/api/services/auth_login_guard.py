"""Login abuse protection with a Redis-backed counter and local fallback.

The guard is deliberately independent from the user table: unknown usernames
must be rate-limited too, otherwise an attacker can rotate usernames to bypass
the control. Redis is used when ``REDIS_URL`` is configured; the bounded local
fallback keeps single-process development protected when Redis is unavailable.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    import redis
except ImportError:  # pragma: no cover - redis is an API runtime dependency
    redis = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LoginThrottleDecision:
    blocked: bool
    retry_after: int = 0


class LoginAttemptGuard:
    """Bounded login failure counter.

    Limits are read at call time so tests and profile-specific deployments can
    override them without re-importing the API module. The Redis key contains a
    SHA-256 digest instead of the username/IP to avoid leaking identifiers in
    shared Redis inspection tools.
    """

    _lock = threading.RLock()
    _memory: dict[str, tuple[int, float]] = {}
    _redis_client: Any = None
    _redis_url: str | None = None
    _redis_unavailable_until = 0.0

    @classmethod
    def max_failures(cls) -> int:
        return _int_env("SIQ_AUTH_LOGIN_MAX_FAILURES", 5)

    @classmethod
    def ip_max_failures(cls) -> int:
        return _int_env("SIQ_AUTH_LOGIN_IP_MAX_FAILURES", 25)

    @classmethod
    def window_seconds(cls) -> int:
        return _int_env("SIQ_AUTH_LOGIN_WINDOW_SECONDS", 300)

    @classmethod
    def lockout_seconds(cls) -> int:
        return _int_env("SIQ_AUTH_LOGIN_LOCKOUT_SECONDS", 900)

    @classmethod
    def memory_max_entries(cls) -> int:
        return _int_env("SIQ_AUTH_LOGIN_MEMORY_MAX_ENTRIES", 10_000)

    @classmethod
    def _key(cls, kind: str, value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()
        return f"siq:auth:login:{kind}:{digest}"

    @classmethod
    def identity_keys(cls, username: str, ip_address: str | None) -> tuple[str, str]:
        normalized_username = str(username or "").strip().casefold()[:128]
        normalized_ip = str(ip_address or "unknown").strip()[:128]
        return (
            cls._key("user", f"{normalized_ip}\0{normalized_username}"),
            cls._key("ip", normalized_ip),
        )

    @classmethod
    def _redis(cls):
        url = str(os.getenv("REDIS_URL") or "").strip()
        if not url or redis is None:
            return None
        now = time.monotonic()
        with cls._lock:
            if cls._redis_client is not None and cls._redis_url == url:
                return cls._redis_client
            if now < cls._redis_unavailable_until and cls._redis_url == url:
                return None
            try:
                client = redis.from_url(
                    url,
                    socket_connect_timeout=0.25,
                    socket_timeout=0.25,
                    health_check_interval=30,
                    decode_responses=True,
                )
                client.ping()
            except Exception:
                cls._redis_client = None
                cls._redis_url = url
                cls._redis_unavailable_until = now + 30
                logger.warning("login_rate_limit_redis_unavailable")
                return None
            cls._redis_client = client
            cls._redis_url = url
            cls._redis_unavailable_until = 0.0
            return client

    @classmethod
    def _mark_redis_unavailable(cls) -> None:
        with cls._lock:
            cls._redis_client = None
            cls._redis_unavailable_until = time.monotonic() + 30

    @classmethod
    def _memory_get(cls, key: str, now: float) -> tuple[int, int]:
        with cls._lock:
            count, expires_at = cls._memory.get(key, (0, now))
            if expires_at <= now:
                cls._memory.pop(key, None)
                return 0, 0
            return count, max(1, int(expires_at - now))

    @classmethod
    def _memory_set(cls, key: str, value: tuple[int, float], now: float) -> None:
        with cls._lock:
            if key not in cls._memory and len(cls._memory) >= cls.memory_max_entries():
                # Remove expired records first, then evict oldest records. The
                # fallback is a safety net, not a durable audit store, so a
                # bounded cache is preferable to process memory exhaustion.
                for stale_key, (_count, expires_at) in list(cls._memory.items()):
                    if expires_at <= now:
                        cls._memory.pop(stale_key, None)
                        if len(cls._memory) < cls.memory_max_entries():
                            break
                while len(cls._memory) >= cls.memory_max_entries():
                    cls._memory.pop(next(iter(cls._memory)), None)
            cls._memory[key] = value

    @classmethod
    def _memory_increment(cls, key: str, now: float) -> tuple[int, int]:
        with cls._lock:
            count, expires_at = cls._memory.get(key, (0, now))
            if expires_at <= now:
                count, expires_at = 0, now + cls.window_seconds()
            count += 1
            cls._memory_set(key, (count, expires_at), now)
            return count, max(1, int(expires_at - now))

    @classmethod
    def check(cls, username: str, ip_address: str | None) -> LoginThrottleDecision:
        keys = cls.identity_keys(username, ip_address)
        thresholds = (cls.max_failures(), cls.ip_max_failures())
        client = cls._redis()
        if client is not None:
            try:
                blocked_ttls = [int(client.ttl(f"{key}:blocked")) for key in keys]
                blocked_ttls = [ttl for ttl in blocked_ttls if ttl > 0]
                if blocked_ttls:
                    return LoginThrottleDecision(True, max(blocked_ttls))
                counts = [int(client.get(key) or 0) for key in keys]
                if any(count >= threshold for count, threshold in zip(counts, thresholds, strict=True)):
                    retry_after = max(int(client.ttl(key)) for key in keys)
                    return LoginThrottleDecision(True, max(1, retry_after))
            except Exception:
                cls._mark_redis_unavailable()
                logger.warning("login_rate_limit_redis_read_failed", exc_info=True)

        now = time.monotonic()
        blocked_values = [cls._memory_get(f"{key}:blocked", now) for key in keys]
        if any(count for count, _ in blocked_values):
            return LoginThrottleDecision(True, max(ttl for _, ttl in blocked_values))
        memory_values = [cls._memory_get(key, now) for key in keys]
        if any(
            count >= threshold
            for (count, _), threshold in zip(memory_values, thresholds, strict=True)
        ):
            return LoginThrottleDecision(True, max(ttl for _, ttl in memory_values))
        return LoginThrottleDecision(False)

    @classmethod
    def record_failure(cls, username: str, ip_address: str | None) -> LoginThrottleDecision:
        keys = cls.identity_keys(username, ip_address)
        thresholds = (cls.max_failures(), cls.ip_max_failures())
        client = cls._redis()
        if client is not None:
            try:
                counts: list[int] = []
                for key, threshold in zip(keys, thresholds, strict=True):
                    count = int(client.incr(key))
                    counts.append(count)
                    if count == 1:
                        client.expire(key, cls.window_seconds())
                    if count >= threshold:
                        client.setex(f"{key}:blocked", cls.lockout_seconds(), "1")
                if any(count >= threshold for count, threshold in zip(counts, thresholds, strict=True)):
                    return LoginThrottleDecision(True, cls.lockout_seconds())
                return LoginThrottleDecision(False)
            except Exception:
                cls._mark_redis_unavailable()
                logger.warning("login_rate_limit_redis_write_failed", exc_info=True)

        now = time.monotonic()
        counts = [cls._memory_increment(key, now)[0] for key in keys]
        if any(count >= threshold for count, threshold in zip(counts, thresholds, strict=True)):
            with cls._lock:
                for key, count, threshold in zip(keys, counts, thresholds, strict=True):
                    if count >= threshold:
                        cls._memory_set(
                            f"{key}:blocked",
                            (1, now + cls.lockout_seconds()),
                            now,
                        )
            return LoginThrottleDecision(True, cls.lockout_seconds())
        return LoginThrottleDecision(False)

    @classmethod
    def clear_user(cls, username: str, ip_address: str | None) -> None:
        user_key, _ip_key = cls.identity_keys(username, ip_address)
        client = cls._redis()
        if client is not None:
            try:
                client.delete(user_key, f"{user_key}:blocked")
                return
            except Exception:
                cls._mark_redis_unavailable()
                logger.warning("login_rate_limit_redis_clear_failed", exc_info=True)
        with cls._lock:
            cls._memory.pop(user_key, None)
            cls._memory.pop(f"{user_key}:blocked", None)

    @classmethod
    def reset_for_tests(cls) -> None:
        """Clear local state; tests must not mutate a real Redis instance."""
        with cls._lock:
            cls._memory.clear()
            cls._redis_client = None
            cls._redis_url = None
            cls._redis_unavailable_until = 0.0
