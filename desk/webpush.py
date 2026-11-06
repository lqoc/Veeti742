"""Web Push with VAPID to every subscribed iPhone (db.push_subs). 404/410 = subscription gone, delete it."""
from __future__ import annotations

import asyncio
import json
import logging
import os

from . import db, vault

log = logging.getLogger("webpush")


def _send_one(sub: dict, payload: dict) -> int:
    from pywebpush import WebPushException, webpush

    try:
        r = webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=json.dumps(payload),
            vapid_private_key=vault.vapid_private_path(),
            vapid_claims={"sub": os.environ.get("VAPID_SUB", "mailto:grok-desk@example.com")},
            ttl=3600,
        )
        return getattr(r, "status_code", 201)
    except WebPushException as e:
        return getattr(e.response, "status_code", 0) or 0
    except Exception as e:  # network, bad key
        log.warning("push failed: %s", e.__class__.__name__)
        return 0


async def send_all(payload: dict) -> dict:
    subs = db.query("SELECT endpoint, p256dh, auth FROM push_subs")
    sent = failed = 0
    for sub in subs:
        code = await asyncio.to_thread(_send_one, sub, payload)
        if code in (404, 410):
            db.execute("DELETE FROM push_subs WHERE endpoint=?", (sub["endpoint"],))
            failed += 1
        elif 200 <= code < 300:
            sent += 1
        else:
            failed += 1
    return {"subs": len(subs), "sent": sent, "failed": failed}


def save(sub: dict) -> None:
    keys = sub.get("keys") or {}
    if not sub.get("endpoint", "").startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("bad subscription")
    db.execute("INSERT INTO push_subs(endpoint, p256dh, auth, created_at) VALUES(?,?,?,strftime('%s','now')) "
               "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth",
               (sub["endpoint"], keys["p256dh"], keys["auth"]))
