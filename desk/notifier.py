"""Every push the desk sends. No Telegram: in-app bell + Web Push to the home-screen app.

Each push carries one URL and it is the exact string links.py returned (coin_url for coins).
"""
from __future__ import annotations

import logging
import time

from . import bus, config, db, links, webpush

log = logging.getLogger("notifier")


def paper_prefix() -> str:
    return "PAPER · " if config.get("desk.mode") == "PAPER" else ""


async def push(line: str, mint: str | None = None, url: str | None = None, trade: bool = True,
               title: str = "Grok Bot") -> dict:
    body = (paper_prefix() if trade else "") + line
    if url is None and mint:
        url = links.coin_url(mint, "solana")
    nid = db.execute("INSERT INTO notifications(ts, title, body, url, mint) VALUES(?,?,?,?,?)",
                     (time.time(), title, body, url, mint))
    payload = {"id": nid, "title": title, "body": body, "url": url, "mint": mint,
               "icon": "/icons/icon-192.png", "button": "Open on FOMO" if links.is_fomo(url) else "Open"}
    bus.publish("push", payload)
    result = {"id": nid, "body": body, "url": url, "webpush": None}
    if config.get("desk.notifier") == "webpush":
        try:
            result["webpush"] = await webpush.send_all(payload)
        except Exception as e:
            log.warning("web push error: %s", e.__class__.__name__)
    log.info("push %s | %s", body, url)
    return result
