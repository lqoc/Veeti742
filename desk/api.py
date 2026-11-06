"""JSON + SSE for the app (STEP 7, plan A). Every /api call needs APP_TOKEN (Authorization: Bearer), else 401."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import (book, bus, commands, config, control, db, fields, learn, links, notifier, registry, sources, vault,
               wallet, webpush)
from .agents.auditor import LAYERS
from .agents.risk import exit_lines
from .agents.scanner import record_prices

log = logging.getLogger("api")
WEB = Path(__file__).resolve().parent / "web"
RANGES = {"LIVE": None, "1H": 3600, "4H": 4 * 3600, "1D": 86400, "1W": 7 * 86400, "ALL": None}
viewed: dict[str, float] = {}

app = FastAPI(title="Grok Bot desk", docs_url=None, redoc_url=None, openapi_url=None)


def auth(request: Request) -> None:
    token = vault.get("APP_TOKEN") or ""
    got = request.headers.get("authorization", "")
    if not got.startswith("Bearer ") or not hmac.compare_digest(got[7:].encode(), token.encode()):
        raise HTTPException(401, "unauthorized")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        db.state_set("public_url", f"{proto}://{host}")


A = [Depends(auth)]


def err(status: int, msg: str):
    return JSONResponse({"error": msg}, status_code=status)


# ------------------------------------------------------------------ state
def ticker() -> list[dict]:
    rows = db.query("SELECT c.mint, c.symbol, c.name, c.image FROM coins c WHERE c.state IN ('passed','waiting') "
                    "AND c.symbol IS NOT NULL ORDER BY c.first_seen DESC LIMIT 12")
    out = []
    for r in rows:
        px = db.query("SELECT price FROM prices WHERE mint=? ORDER BY ts DESC LIMIT 2", (r["mint"],))
        if not px:
            continue
        first = db.one("SELECT price FROM prices WHERE mint=? ORDER BY ts ASC LIMIT 1", (r["mint"],))
        ch = (px[0]["price"] / first["price"] - 1) * 100 if first and first["price"] else 0
        out.append({"mint": r["mint"], "symbol": r["symbol"], "price": px[0]["price"], "change": round(ch, 1),
                    "image": r["image"]})
    return out


@app.get("/api/state", dependencies=A)
async def state():
    b = book.breakdown()
    ns_eq = book.night_start_equity()
    beats = control.heartbeats()
    ns = book.night_start()
    counts = {
        "scanner": db.scalar("SELECT COUNT(*) FROM coins WHERE first_seen>=?", (ns,), 0),
        "auditor": db.scalar("SELECT COUNT(DISTINCT run_id) FROM audits WHERE ts>=?", (ns,), 0),
        "analyst": db.scalar("SELECT COUNT(*) FROM verdicts WHERE ts>=?", (ns,), 0),
        "executor": db.scalar("SELECT COUNT(*) FROM signals WHERE ts>=? AND status='bought'", (ns,), 0),
        "risk": db.scalar("SELECT COUNT(*) FROM positions WHERE closed_at>=?", (ns,), 0),
    }
    cards = []
    for name in registry.ORDER:
        a = registry.get(name)
        c = a.card()
        c["paused"] = a.paused
        c["beat_age"] = round(beats.get(name, 1e9), 1)
        c["count"] = counts[name]
        cards.append(c)
    running = sum(1 for n in registry.ORDER if beats.get(n, 1e9) < 120 and db.state_get(f"paused.{n}", "0") != "1")
    w = book.wallet_cached()
    return {
        "mode": book.mode(), "venue": config.get("desk.venue"), "balance": b["equity"], "breakdown": b,
        "today_pct": round((b["equity"] / ns_eq - 1) * 100, 2) if ns_eq else 0.0,
        "multiplier": b["multiplier"], "started_with": b["start"], "agents": cards, "running": running,
        "unread": db.scalar("SELECT COUNT(*) FROM notifications WHERE read=0", (), 0),
        "ticker": ticker(), "wallet": {"pubkey": wallet.pubkey(), "sol": w.get("sol"), "usdc": w.get("usdc")},
        "ticket": config.ticket_check(book.stack_value()), "ts": time.time(),
        "open_positions": len(book.open_positions(all_modes=True)),
        "stop_pct": config.get("risk.stop_pct"), "trail_pct": config.get("risk.trail_pct"),
    }


@app.get("/api/chart", dependencies=A)
async def chart(range: str = "LIVE"):
    rng = range.upper()
    if rng not in RANGES:
        return err(400, "range is LIVE, 1H, 4H, 1D, 1W or ALL")
    now = time.time()
    since = book.night_start() if rng == "LIVE" else (book.run_start() if rng == "ALL" else now - RANGES[rng])
    rows = db.query("SELECT ts, value FROM balance WHERE mode=? AND ts>=? ORDER BY ts", (book.mode(), since))
    if len(rows) > 400:
        step = len(rows) / 400
        rows = [rows[int(i * step)] for i in range(400)] + [rows[-1]]
    pts = [[r["ts"], round(r["value"], 4)] for r in rows]
    pts.append([now, book.equity()])
    marks = []
    for p in db.query("SELECT * FROM positions WHERE mode=? AND (opened_at>=? OR closed_at>=?) ORDER BY opened_at",
                      (book.mode(), since, since)):
        sym = p["symbol"] or p["name"] or "?"
        if p["opened_at"] >= since:
            marks.append({"ts": p["opened_at"], "kind": "buy", "text": f"Bought {sym}", "mint": p["mint"]})
        if p["status"] == "closed" and p["closed_at"] >= since:
            pnl = p["pnl_usd"] or 0
            if pnl >= 0:
                marks.append({"ts": p["closed_at"], "kind": "win", "text": f"+${pnl:.2f}", "mint": p["mint"]})
            else:
                txt = f"-{p['stop_pct']:g}% · -${abs(pnl):.2f}" if p["exit_reason"] == "stop" else f"-${abs(pnl):.2f}"
                marks.append({"ts": p["closed_at"], "kind": "loss", "text": txt, "mint": p["mint"]})
    return {"range": rng, "points": pts, "start": book.run_start_equity() if rng == "ALL" else (pts[0][1] if pts else 0),
            "marks": marks}


def act_row(r: dict) -> dict:
    coin = db.one("SELECT image, symbol FROM coins WHERE mint=?", (r["mint"],)) or {}
    return {**r, "link": links.coin_url(r["mint"], "solana"), "image": coin.get("image"), "symbol": coin.get("symbol")}


@app.get("/api/activity", dependencies=A)
async def activity(before: int | None = None, limit: int = 30):
    limit = max(1, min(limit, 30))
    if before:
        rows = db.query("SELECT * FROM activity WHERE id<? ORDER BY id DESC LIMIT ?", (before, limit))
    else:
        rows = db.query("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,))
    return {"rows": [act_row(r) for r in rows], "next": rows[-1]["id"] if len(rows) == limit else None}


def candles(mint: str, since: float) -> list[dict]:
    rows = db.query("SELECT ts, price, volume_m5 FROM prices WHERE mint=? AND ts>=? ORDER BY ts", (mint, since))
    out: list[dict] = []
    prev_v = None
    for r in rows:
        minute = int(r["ts"] // 60) * 60
        dv = 0.0 if prev_v is None or r["volume_m5"] is None else max(0.0, r["volume_m5"] - prev_v)
        prev_v = r["volume_m5"] if r["volume_m5"] is not None else prev_v
        if out and out[-1]["t"] == minute:
            c = out[-1]
            c["h"] = max(c["h"], r["price"])
            c["l"] = min(c["l"], r["price"])
            c["c"] = r["price"]
            c["v"] += dv
        else:
            out.append({"t": minute, "o": r["price"], "h": r["price"], "l": r["price"], "c": r["price"], "v": dv})
    return out[-240:]


def last_audit(mint: str) -> list[dict]:
    run = db.one("SELECT run_id FROM audits WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))
    if not run:
        return []
    return db.query("SELECT layer, name, value_text, pass, source, ts FROM audits WHERE run_id=? ORDER BY layer",
                    (run["run_id"],))


@app.get("/api/coin/{mint}", dependencies=A)
async def coin(mint: str, fresh: bool = True):
    c = db.one("SELECT * FROM coins WHERE mint=?", (mint,)) or {"mint": mint}
    v = {}
    if fresh:   # the app asks live; the deed.page publisher builds from what the desk already recorded
        viewed[mint] = time.time()
        try:
            pairs = await sources.dex_tokens([mint])
            record_prices(pairs)
            v = sources.pair_view(pairs.get(mint))
        except sources.SourceError:
            v = {}
    else:
        snap = db.one("SELECT snapshot FROM scans WHERE mint=? AND snapshot IS NOT NULL ORDER BY id DESC LIMIT 1",
                      (mint,))
        try:
            v = json.loads(snap["snapshot"]) if snap else {}
        except ValueError:
            v = {}
        f = db.one("SELECT price, liq, mcap FROM followups WHERE mint=? ORDER BY ts DESC LIMIT 1", (mint,))
        if f:
            v.update({k: f[k2] for k, k2 in (("price", "price"), ("liq", "liq"), ("mcap", "mcap")) if f[k2] is not None})
        v.setdefault("name", c.get("name"))
        v.setdefault("symbol", c.get("symbol"))
    pos = db.one("SELECT * FROM positions WHERE mint=? AND status IN ('open','exiting') ORDER BY id DESC LIMIT 1",
                 (mint,))
    last_closed = None if pos else db.one("SELECT * FROM positions WHERE mint=? AND status='closed' "
                                           "ORDER BY id DESC LIMIT 1", (mint,))
    layers = last_audit(mint)
    shield = f"{sum(1 for x in layers if x['pass'])}/8" if layers else None
    price = v.get("price") or (db.scalar("SELECT price FROM prices WHERE mint=? ORDER BY ts DESC LIMIT 1", (mint,)))
    since = (pos or {}).get("opened_at", time.time() - 3 * 3600) - 1800
    created = v.get("created") / 1000 if v.get("created") else c.get("migrated_at")
    out = {"mint": mint, "name": v.get("name") or c.get("name"), "symbol": v.get("symbol") or c.get("symbol"),
           "image": v.get("image") or c.get("image"), "age_h": round((time.time() - created) / 3600, 1) if created else None,
           "price": price, "mcap": v.get("mcap"), "liq": v.get("liq"), "v24": v.get("v24"), "p24": v.get("p24"),
           "holders": c.get("holders"), "shield": shield, "candles": candles(mint, since),
           "coin_url": links.coin_url(mint, "solana"), "mode": book.mode(), "position": None}
    if pos:
        out["position"] = {**pos, "value": book.position_value(pos), "lines": exit_lines(pos),
                           "since_entry_pct": round(((price or pos["entry_price"]) / pos["entry_price"] - 1) * 100, 2),
                           "tx": links.tx_url(pos["buy_sig"]) if pos.get("buy_sig") else None}
    elif last_closed:
        out["closed"] = last_closed
    return out


@app.get("/api/audit/{mint}", dependencies=A)
async def audit(mint: str):
    viewed[mint] = time.time()
    c = db.one("SELECT * FROM coins WHERE mint=?", (mint,)) or {"mint": mint}
    rows = {r["layer"]: r for r in last_audit(mint)}
    layers = []
    for n, name in enumerate(LAYERS, start=1):
        r = rows.get(n)
        layers.append({"n": n, "name": name, "pass": None if not r else bool(r["pass"]),
                       "value": r["value_text"] if r else "not checked", "source": r["source"] if r else None})
    red = next((x for x in layers if x["pass"] is False), None)
    sym = c.get("symbol") or c.get("name") or mint[:6]
    sig = db.one("SELECT size_usd, status FROM signals WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))
    if not rows:
        verdict = None
    elif red:
        verdict = {"passed": False, "title": f"Rejected · {red['value']}", "sub": f"Grok Bot won't buy {sym}"}
    elif len(rows) == 8:
        sub = f"Sent to Executor · ${sig['size_usd']:.2f}" if sig and sig["size_usd"] else "Sent to Analyst"
        verdict = {"passed": True, "title": "Cleared · 8/8 parts", "sub": sub}
    else:
        verdict = None
    return {"mint": mint, "name": c.get("name"), "symbol": c.get("symbol"), "image": c.get("image"),
            "layers": layers, "verdict": verdict, "enabled": config.get("auditor.enabled"),
            "coin_url": links.coin_url(mint, "solana")}


@app.post("/api/audit/{mint}/run", dependencies=A)
async def audit_run(mint: str):
    c = db.one("SELECT * FROM coins WHERE mint=?", (mint,)) or {}
    asyncio.create_task(registry.get("auditor").audit(
        {"mint": mint, "pool": c.get("pool"), "name": c.get("name"), "symbol": c.get("symbol")}, ad_hoc=True))
    return {"started": True}


@app.get("/api/agents/{name}/decisions", dependencies=A)
async def decisions(name: str):
    if name not in registry.ORDER:
        return err(404, "no such agent")
    rows = registry.get(name).decisions(20)
    for r in rows:
        r["link"] = links.coin_url(r["mint"], "solana") if r.get("mint") else None
    return {"agent": name, "rows": rows}


@app.post("/api/agents/{name}/{action}", dependencies=A)
async def agent_action(name: str, action: str):
    if action not in ("pause", "resume"):
        return err(404, "pause or resume")
    try:
        return control.set_paused(name, action == "pause")
    except control.ControlError as e:
        return err(409, str(e))


# ------------------------------------------------------------------ account / settings / mode
@app.get("/api/account", dependencies=A)
async def account():
    h = config.get("desk.fomo_handle")
    return {"mode": book.mode(), "venue": config.get("desk.venue"), "stack": config.get("desk.stack"),
            "profile_url": links.profile_url(h) if h else None, "fomo_handle": h, "signup_url": links.SIGNUP_URL,
            "wallet": wallet.meta(), "wallet_balances": book.wallet_cached(),
            "wallet_link": links.account_url(wallet.pubkey()) if wallet.pubkey() else None,
            "jev": bool(vault.get("TYPESAFE_API_KEY")), "analyst_mode": config.get("analyst.mode"),
            "notifier": config.get("desk.notifier"), "push_subs": db.scalar("SELECT COUNT(*) FROM push_subs", (), 0),
            "vapid_public": vault.get("VAPID_PUBLIC_KEY"), "paper_nights": control.paper_nights(),
            "live_allowed": control.paper_nights() >= 1, "public_url": db.state_get("public_url"),
            "ticket": config.ticket_check(book.stack_value()), "rpc_url": config.get("desk.rpc_url")}


@app.get("/api/settings", dependencies=A)
async def settings_get():
    return {"settings": config.describe(), "groups": config.GROUPS}


@app.post("/api/settings", dependencies=A)
async def settings_post(request: Request):
    body = await request.json()
    changes = body.get("changes") if isinstance(body.get("changes"), dict) else (
        {body["key"]: body.get("value")} if "key" in body else body)
    out = []
    for k, v in changes.items():
        if k in config.GUARDED:
            return err(400, f"{k} has its own switch")
        try:
            old, new = config.set_value(k, v, "app")
            out.append({"key": k, "old": old, "new": new, "text": f"{config.display(k, old)} -> {config.display(k, new)}"})
        except config.SettingError as e:
            return err(400, str(e))
    ticket = config.ticket_check(book.stack_value())
    return {"changed": out, "ticket": ticket}


@app.post("/api/mode", dependencies=A)
async def mode(request: Request):
    body = await request.json()
    try:
        return await control.set_mode(body.get("mode"), body.get("confirm"), "app")
    except control.ControlError as e:
        return err(400, str(e))


@app.post("/api/run/reset", dependencies=A)
async def run_reset(request: Request):
    body = await request.json()
    if body.get("confirm") != "RESET":
        return err(400, "type RESET to confirm")
    if book.mode() == "PAPER" and book.open_positions():
        return err(409, "close PAPER positions first")
    if book.live_wallet():
        await book.refresh_wallet()
    book.start_run()
    return {"ok": True, "breakdown": book.breakdown()}


@app.post("/api/jev", dependencies=A)
async def jev_key(request: Request):
    body = await request.json()
    key = (body.get("key") or "").strip() or None
    vault.set_secret("TYPESAFE_API_KEY", key)
    config.set_value("analyst.mode", "jev" if key else "rules", "app")
    if not key:
        return {"jev": False, "analyst_mode": "rules"}
    from .agents.analyst import JevError, ask_jev
    try:
        ans, model, tokens, usage = await ask_jev("Coin TEST on Solana, graduated 30 min ago. Liquidity $50,000.")
        return {"jev": True, "analyst_mode": "jev", "test": {"answers": ans, "model": model, "usage": usage}}
    except JevError as e:
        return {"jev": True, "analyst_mode": "jev", "test": {"error": str(e).split("|")[0]}}


# ------------------------------------------------------------------ wallet
@app.get("/api/wallet", dependencies=A)
async def wallet_get():
    m = wallet.meta()
    if not m:
        return {"wallet": None}
    try:
        b = await book.refresh_wallet()
    except sources.SourceError as e:
        b = {**book.wallet_cached(), "error": str(e)}
    return {"wallet": m, "balances": b, "link": links.account_url(m["pubkey"])}


@app.post("/api/wallet/import", dependencies=A)
async def wallet_import(request: Request):
    body = await request.json()
    if db.scalar("SELECT COUNT(*) FROM positions WHERE mode='LIVE' AND venue='wallet' AND status IN ('open','exiting')",
                 (), 0):
        return err(409, "LIVE wallet positions are open. Sell them before changing wallets.")
    try:
        pub = wallet.import_wallet(body.get("secret") or "")
    except wallet.WalletError as e:
        return err(400, str(e))
    try:
        b = await book.refresh_wallet()
    except sources.SourceError:
        b = {}
    return {"pubkey": pub, "balances": b, "link": links.account_url(pub)}


@app.post("/api/wallet/remove", dependencies=A)
async def wallet_remove(request: Request):
    body = await request.json()
    if body.get("confirm") != "REMOVE":
        return err(400, "type REMOVE to confirm")
    if db.scalar("SELECT COUNT(*) FROM positions WHERE mode='LIVE' AND venue='wallet' AND status IN ('open','exiting')",
                 (), 0):
        return err(409, "LIVE wallet positions are open. Sell them first.")
    if book.live_wallet():
        config.set_value("desk.mode", "PAPER", "wallet-remove")
    wallet.remove()
    await book.refresh_wallet()
    return {"wallet": None, "mode": book.mode()}


# ------------------------------------------------------------------ positions
@app.post("/api/positions/{pid}/sell", dependencies=A)
async def position_sell(pid: int):
    r = await registry.get("risk").manual_exit(pid)
    return r if r["ok"] else err(404, r["reason"])


@app.post("/api/sell_all", dependencies=A)
async def sell_all(request: Request):
    body = await request.json()
    if body.get("confirm") != "SELL":
        return err(400, "type SELL to confirm")
    ids = [p["id"] for p in book.open_positions(all_modes=True)]
    for pid in ids:
        await registry.get("risk").manual_exit(pid)
    return {"selling": ids}


@app.post("/api/positions/{pid}/sold", dependencies=A)
async def position_sold(pid: int):
    return {"ok": registry.get("risk").tap_sold(pid)}


@app.post("/api/signals/{sid}/{action}", dependencies=A)
async def signal_action(sid: int, action: str):
    ex = registry.get("executor")
    if action == "bought":
        pid = ex.tap_bought(sid)
        return {"position": pid} if pid else err(409, "signal is not open")
    if action == "skip":
        ex.tap_skip(sid)
        return {"ok": True}
    return err(404, "bought or skip")


@app.get("/api/export", dependencies=A)
async def export(hours: float = 24.0):
    """Everything the desk saw and did, with what each coin did afterwards. For tuning the settings."""
    hours = max(0.25, min(hours, 24 * 14))
    data = learn.export(hours)
    name = time.strftime("grokbot-results-%Y%m%d-%H%M.json", time.gmtime())
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ push / notifications / commands / search
@app.get("/api/push/key", dependencies=A)
async def push_key():
    return {"key": vault.get("VAPID_PUBLIC_KEY")}


@app.post("/api/push", dependencies=A)
async def push_save(request: Request):
    try:
        webpush.save(await request.json())
    except (ValueError, KeyError) as e:
        return err(400, str(e))
    return {"ok": True, "subs": db.scalar("SELECT COUNT(*) FROM push_subs", (), 0)}


@app.post("/api/push/test", dependencies=A)
async def push_test():
    last = db.one("SELECT mint FROM coins WHERE symbol IS NOT NULL ORDER BY first_seen DESC LIMIT 1")
    if last:
        res = await notifier.push("Test push · your desk is connected", mint=last["mint"])
    else:
        res = await notifier.push("Test push · your desk is connected", url=links.SIGNUP_URL)
    return res


@app.get("/api/notifications", dependencies=A)
async def notifications():
    rows = db.query("SELECT * FROM notifications ORDER BY id DESC LIMIT 50")
    return {"rows": rows, "unread": db.scalar("SELECT COUNT(*) FROM notifications WHERE read=0", (), 0)}


@app.post("/api/notifications/read", dependencies=A)
async def notifications_read():
    db.execute("UPDATE notifications SET read=1 WHERE read=0")
    return {"unread": 0}


@app.post("/api/command", dependencies=A)
async def command(request: Request):
    body = await request.json()
    return await commands.handle(body.get("text") or "")


@app.get("/api/search", dependencies=A)
async def search(q: str = ""):
    rows = control.find_coins(q)[:10] if q.strip() else []
    return {"rows": [{"mint": r["mint"], "name": r.get("name"), "symbol": r.get("symbol"), "image": r.get("image"),
                      "link": links.coin_url(r["mint"], "solana")} for r in rows]}


@app.get("/api/stream", dependencies=A)
async def stream(request: Request):
    sq = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(sq.get(), timeout=15)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(sq)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ public
@app.get("/healthz")
async def healthz():
    beats = control.heartbeats()
    stale = [n for n in registry.ORDER if beats.get(n, 1e9) > 180]
    return JSONResponse({"ok": not stale, "stale": stale}, status_code=200 if not stale else 503)


@app.get("/report/{rid}")
async def report_page(rid: str):
    r = db.one("SELECT path FROM reports WHERE id=?", (rid,))
    if not r or not Path(r["path"]).exists():
        raise HTTPException(404)
    return HTMLResponse(Path(r["path"]).read_text(encoding="utf-8"))


@app.get("/sw.js")
async def sw():
    return FileResponse(WEB / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB), name="web")


# ------------------------------------------------------------------ api.py price writer
async def price_loop() -> None:
    """Every 60s: candles for coins pushed, audited or opened in the app tonight that Scanner and Risk don't cover."""
    while True:
        await asyncio.sleep(60)
        try:
            ns = book.night_start()
            cutoff = time.time() - 3600
            mints = {m for m, t in viewed.items() if t > cutoff}
            mints |= {r["mint"] for r in db.query("SELECT DISTINCT mint FROM audits WHERE ts>=?", (ns,))}
            mints |= {r["mint"] for r in db.query("SELECT DISTINCT mint FROM signals WHERE ts>=?", (ns,))}
            covered = {r["mint"] for r in db.query("SELECT mint FROM coins WHERE state='waiting'")}
            covered |= {p["mint"] for p in book.open_positions(all_modes=True)}
            todo = sorted(mints - covered)[:90]
            if todo:
                record_prices(await sources.dex_tokens(todo))
            db.heartbeat("api")
        except Exception as e:
            log.info("price loop: %s", e)
