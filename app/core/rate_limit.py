from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.security import decode_token


def _key_func(request: Request) -> str:
    """Rate-limit by user id when authenticated, else by client IP.

    User-keyed limits are fairer for logged-in actions (e.g. /generate)
    where a shared NAT IP would otherwise cap real users.
    """
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        try:
            payload = decode_token(auth[7:].strip())
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_key_func, default_limits=[])
