"""The website on deed.page (static hosting, plan B of the build prompt).

deed.page runs no server code, so the desk pushes its numbers there:
  full publish   the app shell + every data file, at start, every 30 min (prunes old coin files) and when the
                 shell changes. It replaces the whole site.
  data publish   only /d/{DATA_PATH}/*.json, every desk.publish_s seconds (X-Merge: 1).
Free plan: 60 publishes/hour per token, 4 MB per request, 10 versions kept, sites are public.

The page is opened once as https://{slug}.deed.page/#d={DATA_PATH} and is read-only: no key, no token,
no wallet import (that code is cut out of the published app.js). Controls stay in the desk's own app.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import logging
import os
import re
import secrets
import tarfile
import time
from pathlib import Path

import httpx

from . import api, book, config, control, db, learn, links, notifier, registry, sources, vault

log = logging.getLogger("publish")
WEB = Path(__file__).resolve().parent / "web"
API_URL = "https://deed.page/v1/sites"
CLIENT = "Grok Bot desk (Claude Code)"
FULL_EVERY_S = 30 * 60
EXPORT_EVERY_S = 10 * 60
MAX_BODY = 3_800_000
CUT = re.compile(r"/\* WALLET-IMPORT-START \*/.*?/\* WALLET-IMPORT-END \*/", re.S)


class PublishError(Exception):
    pass


# ---------------------------------------------------------------- what goes up
def shell_files() -> dict[str, bytes]:
    out = {}
    for p in sorted(WEB.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(WEB).as_posix()
        data = p.read_bytes()
        if rel == "app.js":
            data = CUT.sub("/* wallet import is only in the desk's own app */", data.decode()).encode()
        out[rel] = data
    return out


def _mask_rpc(url: str) -> str:
    return re.sub(r"\?.*$", "?…", url or "")


def _clean(obj):
    """Nothing secret can ride along: drop keys we never publish."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()
                if k not in ("vapid_public", "public_url", "rpc_url", "buy_sig", "sell_sig", "tokens_raw")}
    if isinstance(obj, list):
        return [_clean(x) for x in obj]
    return obj


def _json(obj) -> bytes:
    return json.dumps(_clean(obj), default=str, separators=(",", ":")).encode()


def pick_mints(limit: int = 40) -> list[str]:
    since = time.time() - 86400
    mints: list[str] = []

    def add(rows):
        for r in rows:
            if r["mint"] not in mints:
                mints.append(r["mint"])

    add(db.query("SELECT mint FROM positions WHERE status IN ('open','exiting')"))
    add(db.query("SELECT mint FROM positions WHERE closed_at>=? ORDER BY closed_at DESC", (since,)))
    add(db.query("SELECT mint FROM activity ORDER BY id DESC LIMIT 30"))
    add(db.query("SELECT DISTINCT mint FROM audits WHERE ts>=? ORDER BY id DESC LIMIT 15", (since,)))
    add(db.query("SELECT mint FROM scans ORDER BY id DESC LIMIT 12"))
    add([{"mint": t["mint"]} for t in api.ticker()])
    return mints[:limit]


async def data_files(with_export: bool) -> dict[str, bytes]:
    d = f"d/{vault.data_path()}/"
    files: dict[str, bytes] = {}
    files[d + "state.json"] = _json(await api.state())
    for r in api.RANGES:
        files[d + f"chart-{r}.json"] = _json(await api.chart(r))
    rows = db.query("SELECT * FROM activity ORDER BY id DESC LIMIT 150")
    files[d + "activity.json"] = _json({"rows": [api.act_row(r) for r in rows], "next": None})
    for n in registry.ORDER:
        files[d + f"decisions-{n}.json"] = _json(await api.decisions(n))
    acct = await api.account()
    w = acct.get("wallet") or None
    acct["wallet"] = {"pubkey": w["pubkey"]} if w else None
    files[d + "account.json"] = _json(acct)
    st = await api.settings_get()
    for s in st["settings"]:
        if s["key"] == "desk.rpc_url":
            s["value"] = _mask_rpc(s["value"])
    files[d + "settings.json"] = _json(st)
    files[d + "notifications.json"] = _json(await api.notifications())
    coins = db.query("SELECT mint, name, symbol, image FROM coins WHERE first_seen>=? ORDER BY first_seen DESC LIMIT 500",
                     (time.time() - 86400,))
    files[d + "coins.json"] = _json({"rows": [{**c, "link": links.coin_url(c["mint"], "solana")} for c in coins]})
    for m in pick_mints():
        files[d + f"coin/{m}.json"] = _json(await api.coin(m, fresh=False))
        files[d + f"audit/{m}.json"] = _json(await api.audit(m))
    if with_export:
        files[d + "export.json"] = _json(learn.export(24))
    files[d + "meta.json"] = _json({"published_at": time.time(), "publish_s": config.get("desk.publish_s"),
                                    "mode": book.mode(), "static": True})
    return files


def tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tf:
        now = int(time.time())
        for path, data in files.items():
            ti = tarfile.TarInfo(path)
            ti.size = len(data)
            ti.mtime = now
            ti.mode = 0o644
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


# ---------------------------------------------------------------- talking to deed.page
def slug() -> str:
    s = config.get("desk.site_slug")
    if not s:
        s = "grokbot-" + secrets.token_hex(4)
        config.set_value("desk.site_slug", s, "publisher")
    return s


def site_url() -> str | None:
    return db.state_get("site_url")


def login_link() -> str | None:
    u = site_url()
    return f"{u.rstrip('/')}/#d={vault.data_path()}" if u else None


async def put(body: bytes, merge: bool, client: httpx.AsyncClient | None = None) -> dict:
    headers = {"X-Client": CLIENT, "Content-Type": "application/gzip"}
    tok = vault.get("DEEDPAGE_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    if merge:
        headers["X-Merge"] = "1"
    c = client or sources.client()
    for attempt in range(3):
        r = await c.put(API_URL, params={"slug": slug(), "ref": "grok-desk"}, content=body, headers=headers,
                        timeout=60)
        try:
            j = r.json()
        except ValueError:
            j = {"error": r.text[:200]}
        if r.status_code in (200, 201) and j.get("ok"):
            if j.get("token") and not tok:
                vault.set_secret("DEEDPAGE_TOKEN", j["token"])     # returned once; never printed or logged
            if j.get("url"):
                db.state_set("site_url", j["url"])
            return j
        err = j.get("error") or j.get("code") or str(r.status_code)
        if r.status_code == 409 and "slug_taken" in str(err) and not tok:
            new = j.get("suggestion") or ("grokbot-" + secrets.token_hex(4))
            config.set_value("desk.site_slug", new, "publisher")
            continue
        if r.status_code == 409 and "version_conflict" in str(err):
            await asyncio.sleep(2)
            continue
        if r.status_code == 429:
            raise PublishError(f"rate limited, retry in {j.get('retry_after_seconds', 60)}s|"
                               f"{j.get('message_for_user') or ''}|{j.get('retry_after_seconds', 60)}")
        raise PublishError(f"HTTP {r.status_code} {err}: {j.get('message_for_user') or j.get('message') or ''}"[:300])
    raise PublishError("gave up after 3 tries")


class Publisher:
    def __init__(self) -> None:
        self.last_full = 0.0
        self.last_export = 0.0
        self.shell_hash = ""
        self.stopped_reason: str | None = None
        self.count = 0

    async def once(self, client: httpx.AsyncClient | None = None) -> dict:
        shell = shell_files()
        h = hashlib.sha256(b"".join(k.encode() + v for k, v in sorted(shell.items()))).hexdigest()
        now = time.time()
        full = (not site_url()) or now - self.last_full > FULL_EVERY_S or h != self.shell_hash
        with_export = full or now - self.last_export > EXPORT_EVERY_S
        files = await data_files(with_export)
        if full:
            files = {**shell, **files}
        body = tarball(files)
        if len(body) > MAX_BODY:   # trim coin pages until it fits in one request
            for k in [k for k in files if "/coin/" in k or "/audit/" in k][::-1]:
                files.pop(k)
                body = tarball(files)
                if len(body) <= MAX_BODY:
                    break
        res = await put(body, merge=not full, client=client)
        if full:
            self.last_full, self.shell_hash = now, h
        if with_export:
            self.last_export = now
        self.count += 1
        db.state_set("site_published_at", now)
        log.info("published %s v%s (%s, %d files, %d KB)", res.get("url"), res.get("version"),
                 "full" if full else "data", len(files), len(body) // 1024)
        return res

    async def loop(self) -> None:
        announced = False
        while True:
            if config.get("desk.site") != "deed":
                await asyncio.sleep(15)
                continue
            wait = config.get("desk.publish_s")
            try:
                await self.once()
                db.heartbeat("publish")
                if not announced:
                    announced = True
                    log.info("website: %s (open the link from `python -m desk.main --print-login`)", site_url())
            except PublishError as e:
                parts = str(e).split("|")
                log.warning("publish failed: %s", parts[0])
                if len(parts) == 3:          # 429: deed.page asked us to wait
                    wait = max(wait, int(float(parts[2] or 60)))
                    if parts[1]:
                        await notifier.push(parts[1], trade=False)
                elif "content_rejected" in parts[0] or "403" in parts[0] or "413" in parts[0]:
                    await notifier.push(f"Website publishing stopped: {parts[0]}", trade=False)
                    config.set_value("desk.site", "off", "publisher")
            except Exception as e:
                log.warning("publish error %s: %s", e.__class__.__name__, e)
            await asyncio.sleep(wait)


publisher = Publisher()
