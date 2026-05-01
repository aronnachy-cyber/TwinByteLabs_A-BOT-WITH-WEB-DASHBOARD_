"""
Byte Labs — License Manager
Handles generation, validation, and assignment of tier license codes.

Tiers: "free" | "premium"
Storage: licenses.json  (falls back gracefully if file doesn't exist)
"""

from __future__ import annotations

import datetime
import json
import os
import secrets
import threading
from typing import Dict, List, Optional, Tuple

_LOCK = threading.Lock()
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


# ── internal helpers ────────────────────────────────────────────────────────

def _load() -> Dict:
    if os.path.exists(_LICENSE_FILE):
        try:
            with open(_LICENSE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(data: Dict) -> None:
    with _LOCK:
        with open(_LICENSE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def _now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ── public API ──────────────────────────────────────────────────────────────

def generate_code(tier: str, note: str = "") -> str:
    """
    Create a new, unused license code for *tier* and persist it.
    Returns the new code string.
    """
    if tier not in TIERS:
        raise ValueError(f"Unknown tier: {tier!r}. Must be one of {TIERS}")

    prefix = "BL-FREE" if tier == "free" else "BL-PRO"
    rand   = f"{secrets.token_hex(3).upper()}-{secrets.token_hex(3).upper()}"
    code   = f"{prefix}-{rand}"

    data = _load()
    data[code] = {
        "tier":        tier,
        "note":        note,
        "created_at":  _now_iso(),
        "assigned_to": None,
        "assigned_at": None,
    }
    _save(data)
    return code


def validate_code(raw_code: str) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Check whether *raw_code* is valid and available.
    Returns (license_dict, None) on success or (None, error_message) on failure.
    """
    code = raw_code.strip().upper()
    data = _load()
    lic  = data.get(code)
    if not lic:
        return None, "Invalid license code. Please double-check and try again."
    if lic.get("assigned_to"):
        return None, "This license code is already in use by another account."
    return lic, None


def assign_code(raw_code: str, discord_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Assign *raw_code* to *discord_id* (idempotent — re-assigning to the same
    user is fine). Returns (tier, None) on success or (None, error_message).
    """
    code = raw_code.strip().upper()
    with _LOCK:
        data = _load()
        lic  = data.get(code)
        if not lic:
            return None, "Invalid license code."
        existing_owner = lic.get("assigned_to")
        if existing_owner and existing_owner != str(discord_id):
            return None, "This license code is already in use by another account."
        lic["assigned_to"] = str(discord_id)
        if not existing_owner:
            lic["assigned_at"] = _now_iso()
        data[code] = lic
        _save(data)
    return lic["tier"], None


def get_user_license(discord_id: str) -> Tuple[Optional[str], Optional[Dict]]:
    """
    Find any license assigned to *discord_id*.
    Returns (code, license_dict) or (None, None).
    """
    discord_id = str(discord_id)
    data = _load()
    for code, lic in data.items():
        if lic.get("assigned_to") == discord_id:
            return code, lic
    return None, None


def get_user_tier(discord_id: str) -> str:
    """
    Convenience: return the tier string for *discord_id*, defaulting to "free".
    """
    _, lic = get_user_license(discord_id)
    if lic:
        return lic.get("tier", "free")
    return "free"


def revoke_code(raw_code: str) -> bool:
    """Unassign a code (does not delete it). Returns True on success."""
    code = raw_code.strip().upper()
    with _LOCK:
        data = _load()
        if code not in data:
            return False
        data[code]["assigned_to"] = None
        data[code]["assigned_at"] = None
        _save(data)
    return True


def delete_code(raw_code: str) -> bool:
    """Permanently remove a code. Returns True if it existed."""
    code = raw_code.strip().upper()
    with _LOCK:
        data = _load()
        if code not in data:
            return False
        del data[code]
        _save(data)
    return True


def list_all() -> Dict:
    """Return the full licenses dict (code → license record)."""
    return _load()


def list_codes_for_tier(tier: str) -> List[Dict]:
    """Return all codes (with `code` key injected) for a given tier."""
    data = _load()
    result = []
    for code, lic in data.items():
        if lic.get("tier") == tier:
            result.append({**lic, "code": code})
    return result
