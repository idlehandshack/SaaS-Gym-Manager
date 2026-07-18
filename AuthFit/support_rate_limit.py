"""
Rate limiting for the login-support modal — separate from AuthFit.rate_limit
(which governs actual login attempts) since the failure modes and keys differ.
"""
from django.core.cache import cache

MAX_REQUESTS = 3
WINDOW_SECONDS = 60 * 60  # 1 hour


def is_support_request_rate_limited(ip: str, phone: str) -> bool:
    key = f"support_req_{ip}_{phone}"
    count = cache.get(key, 0)
    if count >= MAX_REQUESTS:
        return True
    cache.set(key, count + 1, timeout=WINDOW_SECONDS)
    return False