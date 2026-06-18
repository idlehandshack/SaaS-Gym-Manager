# AuthFit/rate_limit.py

from django.core.cache import cache

MAX_ATTEMPTS     = 5     # per phone per window
MAX_IP_ATTEMPTS  = 20    # per IP per window (covers credential stuffing)
LOCKOUT_SECONDS  = 300   # 5-minute window


def _phone_key(phone: str) -> str:
    return f"login_phone:{phone}"

def _ip_key(ip: str) -> str:
    return f"login_ip:{ip}"


def check_login_attempt(ip: str, phone: str) -> bool:
    """
    Returns False if EITHER the phone OR the IP is locked out.
    Call before authenticate(). Fail open on cache errors.
    """
    try:
        phone_attempts = cache.get(_phone_key(phone), 0)
        if phone_attempts >= MAX_ATTEMPTS:
            return False

        ip_attempts = cache.get(_ip_key(ip), 0)
        if ip_attempts >= MAX_IP_ATTEMPTS:
            return False

        return True
    except Exception:
        return True  # fail open — never block a legitimate user on cache failure


def record_failed_attempt(ip: str, phone: str) -> None:
    """
    Increments both the phone counter and the IP counter.
    Uses cache.add + cache.incr to avoid the get→set race condition.
    TTL is reset on each failure so the window slides correctly.
    """
    for key, limit in ((_phone_key(phone), MAX_ATTEMPTS), (_ip_key(ip), MAX_IP_ATTEMPTS)):
        try:
            # cache.add sets only if key doesn't exist — atomic
            added = cache.add(key, 1, timeout=LOCKOUT_SECONDS)
            if not added:
                # Key exists — increment, then reset TTL so window slides
                cache.incr(key)
                cache.expire(key, LOCKOUT_SECONDS)
        except Exception:
            pass


def reset_attempt(ip: str, phone: str) -> None:
    """Clears both counters after a successful login."""
    try:
        cache.delete(_phone_key(phone))
        cache.delete(_ip_key(ip))
    except Exception:
        pass


def get_attempts(ip: str, phone: str) -> dict:
    """
    Returns current attempt counts.
    Useful for showing 'X attempts remaining' in the login view.
    """
    try:
        return {
            "phone":    cache.get(_phone_key(phone), 0),
            "ip":       cache.get(_ip_key(ip), 0),
            "phone_max": MAX_ATTEMPTS,
            "ip_max":    MAX_IP_ATTEMPTS,
        }
    except Exception:
        return {"phone": 0, "ip": 0, "phone_max": MAX_ATTEMPTS, "ip_max": MAX_IP_ATTEMPTS}