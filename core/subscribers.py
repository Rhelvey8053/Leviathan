"""
Subscriber management for the Leviathan newsletter.

Stores subscribers in subscribers.json — each entry has email, unsubscribe token,
join date, and active flag. Keeps it dead-simple; no web server required.

CLI usage:
  python subscribers.py add reed@example.com
  python subscribers.py list
  python subscribers.py remove <token>
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone

_ROOT            = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBSCRIBERS_FILE = os.path.join(_ROOT, "subscribers.json")


def _load() -> list[dict]:
    if not os.path.exists(SUBSCRIBERS_FILE):
        return []
    with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(subs: list[dict]) -> None:
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(subs, f, indent=2)


def add_subscriber(email: str) -> str:
    """Adds a subscriber. Returns their unsubscribe token. Idempotent."""
    email = email.lower().strip()
    subs  = _load()
    for s in subs:
        if s["email"] == email:
            if not s["active"]:
                s["active"] = True
                _save(subs)
                print(f"  Reactivated: {email}")
            else:
                print(f"  Already subscribed: {email}")
            return s["token"]

    token = str(uuid.uuid4())
    subs.append({
        "email":     email,
        "token":     token,
        "active":    True,
        "joined_at": datetime.now(timezone.utc).isoformat(),
        "tier":      "free",
    })
    _save(subs)
    print(f"  Added subscriber: {email}  (token: {token})")
    return token


def remove_subscriber(token: str) -> bool:
    """Deactivates a subscriber by their unsubscribe token."""
    subs = _load()
    for s in subs:
        if s["token"] == token:
            s["active"] = False
            _save(subs)
            print(f"  Unsubscribed: {s['email']}")
            return True
    print(f"  Token not found: {token}")
    return False


def get_active_subscribers() -> list[dict]:
    """Returns all active subscriber dicts (email + token)."""
    return [s for s in _load() if s.get("active")]


def list_subscribers() -> None:
    subs  = _load()
    active = [s for s in subs if s.get("active")]
    inactive = [s for s in subs if not s.get("active")]
    print(f"\nActive subscribers ({len(active)}):")
    for s in active:
        joined = s.get("joined_at", "")[:10]
        print(f"  {s['email']:<40}  joined {joined}  token: {s['token'][:8]}...")
    if inactive:
        print(f"\nUnsubscribed ({len(inactive)}):")
        for s in inactive:
            print(f"  {s['email']}")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python subscribers.py [add <email> | remove <token> | list]")
        sys.exit(1)
    cmd = sys.argv[1].lower()
    if cmd == "add" and len(sys.argv) >= 3:
        add_subscriber(sys.argv[2])
    elif cmd == "remove" and len(sys.argv) >= 3:
        remove_subscriber(sys.argv[2])
    elif cmd == "list":
        list_subscribers()
    else:
        print("Usage: python subscribers.py [add <email> | remove <token> | list]")
