"""Finds the public tunnel URL so the app, the login link and report links use it.

cloudflared serves {"hostname": "..."} at <metrics>/quicktunnel for quick tunnels. For a named tunnel set
PUBLIC_URL instead. The URL is logged; the login token never is (use `python -m desk.main --print-login`).
"""
from __future__ import annotations

import asyncio
import logging
import os

from . import db, sources

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
                    last = url
                    db.state_set("public_url", url)
                    log.info("tunnel up: %s  (login link: python -m desk.main --print-login)", url)
        except Exception:
            pass
        await asyncio.sleep(30)
