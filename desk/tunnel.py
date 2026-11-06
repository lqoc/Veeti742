"""Finds the public tunnel URL so the app, the login link and report links use it.

cloudflared serves {"hostname": "..."} at <metrics>/quicktunnel for quick tunnels. For a named tunnel set
PUBLIC_URL instead. The URL is logged; the login token never is (use `python -m desk.main --print-login`).
"""
from __future__ import annotations

import asyncio
import logging
import os

from . import db, sources, vault, webpush

log = logging.getLogger("tunnel")


async def watch() -> None:
    fixed = os.environ.get("PUBLIC_URL")
    if fixed:
        db.state_set("public_url", fixed.rstrip("/"))
        log.info("public URL %s", fixed)
        return
    metrics = os.environ.get("TUNNEL_METRICS")
    if not metrics:
        return
    last = None
    while True:
        try:
            r = await sources.client().get(metrics.rstrip("/") + "/quicktunnel", timeout=5)
            host = (r.json() or {}).get("hostname") if r.status_code == 200 else None
            if host:
                url = f"https://{host}"
                if url != last:
                    old = db.state_get("public_url")
                    last = url
                    db.state_set("public_url", url)
                    log.info("tunnel up: %s  (login link: python -m desk.main --print-login)", url)
                    if old and old != url:
                        # quick-tunnel URLs change on restart: tell subscribed phones the new address.
                        # Web Push is end-to-end encrypted to the device; this never goes into the bell,
                        # the activity feed or the website files.
                        await webpush.send_all({"title": "Grok Bot", "body": "The desk restarted. Tap to open its new address.",
                                                "url": f"{url}/#t={vault.get('APP_TOKEN')}", "icon": "/icons/icon-192.png"})
        except Exception:
            pass
        await asyncio.sleep(30)
