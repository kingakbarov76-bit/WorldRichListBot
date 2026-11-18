"""Telegram Mini App `initData` validation (HMAC-SHA256).

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Optional
from urllib.parse import parse_qsl


def validate_init_data(
    init_data: str, bot_token: str, max_age: int = 24 * 3600, now: Optional[float] = None
) -> Optional[dict[str, Any]]:
    """Return the Telegram user dict if initData is authentic and fresh, else None."""
    if not init_data or not bot_token:
        return None
    try:
        params = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = params.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    try:
        auth_date = int(params.get("auth_date", "0"))
    except ValueError:
        return None
    if (now if now is not None else time.time()) - auth_date > max_age:
        return None

    try:
        user = json.loads(params.get("user", ""))
    except (ValueError, TypeError):
        return None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        return None
    return user
