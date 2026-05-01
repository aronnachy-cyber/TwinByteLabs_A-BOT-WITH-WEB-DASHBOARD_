"""
Byte Labs — License Manager (v2)
Tier assignments are made directly by Discord ID. No manual codes needed.

Tiers:   "free" | "premium"
Storage: licenses.json (keyed by Discord ID string)

Format:
{
  "123456789012345678": {
    "tier":       "premium",
    "note":       "username or note",
    "granted_at": "2025-01-01T00:00:00Z",
    "granted_by": "admin"
  }
}
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from typing import Dict, List, Optional, Tuple

_LOCK = threading.RLock()
_LICENSE_FILE = os.path.join(os.path.dirname(__file__), "..", "licenses.json")
_LICENSE_FILE = os.path.normpath(_LICENSE_FILE)

TIERS = ("free", "premium")

TIER_LABELS = {
    "free": "Free",
    "premium": "Premium",
}

TIER_FEATURES = {
    "free": [
        "AutoMod (Basic)",
        "Anti-Spam",
        "Welcome Messages",
        "Basic Tickets",
        "Web Dashboard Access",
    ],
    "premium": [
        "Everything in Free",
        "AI-Powered Moderation",
        "Advanced Anti-Raid",
        "Reaction Roles",
        "Giveaways System",
        "Social Alerts",
        "Priority Support",
        "Custom Bot Branding",
        "Server Analytics",
        "Unlimited Configs",
    ],
}

TIER_COLORS = {
    "free": "#5b8cff",
    "premium": "#f59e0b",
}

TIER_ICONS = {
    "free": "fa-bolt",
    "premium": "fa-crown",
}


def _load() -> Dict:
    if os.path.exists(_LICENSE_FILE):
        try:
            with open(_LICENSE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            migrated = {}
            for key, val in data.items():
                if isinstance(val, dict):
                    if key.isdigit():
                        migrated[key] = val
                    else:
                        assigned_to = val.get("assigned_to")
                        if assigned_to:
                            if assigned_to not in migrated:
                                migrated[str(assigned_to)] = {
                                    "tier":       val.get("tier", "free"),
                                    "note":       val.get("note", ""),
                                    "granted_at": val.get("assigned_at") or val.get("created_at", _now_iso()),
                                    "granted_by": "migrated",
                                }
            return migrated
        except Exception:
            pass
    return {}


def _save(data: Dict) -> None:
    with _LOCK:
        with open(_LICENSE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def _now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def grant_tier(discord_id: str, tier: str, note: str = "", granted_by: str = "admin") -> None:
    if tier not in TIERS:
        raise ValueError(f"Unknown tier: {tier!r}")
    discord_id = str(discord_id).strip()
    with _LOCK:
        data = _load()
        existing = data.get(discord_id, {})
        data[discord_id] = {
            "tier":       tier,
            "note":       note,
            "granted_at": existing.get("granted_at", _now_iso()),
            "updated_at": _now_iso(),
            "granted_by": granted_by,
        }
        _save(data)


def revoke_user(discord_id: str) -> bool:
    discord_id = str(discord_id).strip()
    with _LOCK:
        data = _load()
        if discord_id not in data:
            return False
        del data[discord_id]
        _save(data)
    return True


def get_user_tier(discord_id: str) -> str:
    discord_id = str(discord_id).strip()
    data = _load()
    record = data.get(discord_id)
    if record:
        return record.get("tier", "free")
    return "free"


def get_user_record(discord_id: str) -> Optional[Dict]:
    discord_id = str(discord_id).strip()
    data = _load()
    return data.get(discord_id)


def list_all() -> Dict:
    return _load()


def list_by_tier(tier: str) -> List[Dict]:
    data = _load()
    result = []
    for did, rec in data.items():
        if rec.get("tier") == tier:
            result.append({"discord_id": did, **rec})
    return result


def get_user_license(discord_id: str) -> Tuple[Optional[str], Optional[Dict]]:
    discord_id = str(discord_id).strip()
    data = _load()
    rec = data.get(discord_id)
    if rec:
        return discord_id, rec
    return None, None


def assign_code(raw_code: str, discord_id: str) -> Tuple[Optional[str], Optional[str]]:
    return None, "Code-based activation is no longer used."
