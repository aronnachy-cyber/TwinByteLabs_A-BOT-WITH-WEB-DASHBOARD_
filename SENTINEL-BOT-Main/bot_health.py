"""Cross-process bot health checks via the Discord REST API.

The dashboard and the bots may run in separate processes (e.g. on Render
the Flask web service is one container and each bot is a worker). In
that case the in-process checks (`bot.is_ready()`, `_bot_thread.is_alive()`)
will always be False on the dashboard side even though the bot is happily
connected to Discord.

To bridge that gap we ping Discord's REST API with the bot token. If
`GET /users/@me` returns 200 the token is valid and Discord can be
reached -- a strong-enough signal that the bot is "online" for the
purpose of the dashboard's status badge. Results are cached briefly so
we never hammer Discord even if the page auto-refreshes.
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Dict, Tuple

# token -> (timestamp, ok)
_CACHE: Dict[str, Tuple[float, bool]] = {}
_DEFAULT_TTL = 30  # seconds


def token_is_live(token: str, ttl: int = _DEFAULT_TTL, timeout: float = 4.0) -> bool:
    """Return True when the given bot token is valid and Discord is reachable.

    Cached for `ttl` seconds per-token. Network or auth errors return False.
    An empty / missing token always returns False without hitting the network.
    """
    if not token:
        return False
    token = token.strip()
    if not token:
        return False

    now = time.time()
    cached = _CACHE.get(token)
    if cached and (now - cached[0] < ttl):
        return cached[1]

    ok = False
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": "SentinelOS-Dashboard (health-check, 1.0)",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = resp.status == 200
            # Drain a tiny bit so the connection can be reused.
            try:
                resp.read(1024)
            except Exception:
                pass
    except urllib.error.HTTPError as e:
        # 401 = bad token, 429 = rate-limited (treat as unknown -> offline-ish)
        ok = False
        if e.code == 429:
            # If we're being rate-limited, don't cache the failure for the
            # full TTL -- try again in 5s so a momentary throttle doesn't
            # flap the badge to offline for too long.
            _CACHE[token] = (now - (ttl - 5), False)
            return False
    except Exception:
        ok = False

    _CACHE[token] = (now, ok)
    return ok


def invalidate(token: str = "") -> None:
    """Drop cached results so the next call re-checks Discord."""
    if token:
        _CACHE.pop(token.strip(), None)
    else:
        _CACHE.clear()
