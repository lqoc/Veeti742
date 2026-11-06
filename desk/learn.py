"""What works and what doesn't: follow every coin for 6 hours after the desk decided, then export it all.

followup_loop   every 5 min, one DexScreener batch for every coin seen in the last 6.5h (killed, rejected,
                skipped or traded) -> db.followups (price, liquidity, market cap). Never feeds trading.
export(hours)   one JSON file: per coin the stage it reached, why, what each agent saw, the trade if any,
                and what the price did +15m / +1h / +3h / +6h after the decision. Plus a summary by stage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
import time

from . import config, db, links, sources

log = logging.getLogger("learn")
FOLLOW_S = 6.5 * 3600
HORIZONS = {"15m": 900, "1h": 3600, "3h": 3 * 3600, "6h": 6 * 3600}


async def followup_once() -> int:
    since = time.time() - FOLLOW_S
    mints = [r["mint"] for r in db.query("SELECT mint FROM coins WHERE first_seen>=? ORDER BY first_seen", (since,))]
    if not mints:
        return 0
    pairs = await sources.dex_tokens(mints)
    now = time.time()
    rows = []
    for m in mints:
        v = sources.pair_view(pairs.get(m))
        rows.append((m, now, v.get("price"), v.get("liq"), v.get("mcap"), v.get("v5")))
    db.executemany("INSERT INTO followups(mint, ts, price, liq, mcap, volume_m5) VALUES(?,?,?,?,?,?)", rows)
    return len(rows)


async def followup_loop() -> None:
    await asyncio.sleep(90)
    while True:
        try:
            await followup_once()
            db.heartbeat("learn")
        except sources.RateLimited:
            await asyncio.sleep(30)
            continue
        except Exception as e:
            log.info("followup: %s", e)
        await asyncio.sleep(300)


# ---------------------------------------------------------------- export
def _series(mint: str, t0: float) -> list[tuple[float, float | None, float | None]]:
    """(ts, price, liq) from the decision on, merging follow-ups and the candle prices."""
    rows = [(r["ts"], r["price"], r["liq"]) for r in
            db.query("SELECT ts, price, liq FROM followups WHERE mint=? AND ts>=? ORDER BY ts", (mint, t0 - 120))]
    rows += [(r["ts"], r["price"], None) for r in
             db.query("SELECT ts, price FROM prices WHERE mint=? AND ts>=? AND ts<=? ORDER BY ts",
                      (mint, t0 - 120, t0 + FOLLOW_S))]
    rows.sort(key=lambda r: r[0])
    return rows


def mask_url(u):
    """RPC URLs often carry an API key in the query (?api-key=...). Never export it."""
    return re.sub(r"\?.*$", "?…", u) if isinstance(u, str) else u


def _pct(a: float | None, b: float | None) -> float | None:
    if not a or b is None:
        return None
    return round((b / a - 1) * 100, 2)


def outcome(mint: str, t0: float, p0: float | None = None, liq0: float | None = None) -> dict:
    s = _series(mint, t0)
    priced = [(t, p) for t, p, _ in s if p]
    if p0 is None and priced:
        p0 = priced[0][1]
    out: dict = {"p0": p0, "samples": len(s), "tracked_min": round((s[-1][0] - t0) / 60) if s else 0}
    for name, h in HORIZONS.items():
        hit = next(((t, p) for t, p in priced if t >= t0 + h), None)
        out[f"at_{name}_pct"] = _pct(p0, hit[1]) if hit and hit[0] <= t0 + h + 900 else None
    window = [p for t, p in priced if t <= t0 + 6 * 3600]
    out["max_up_pct"] = _pct(p0, max(window)) if window else None
    out["max_down_pct"] = _pct(p0, min(window)) if window else None
    out["last_pct"] = _pct(p0, priced[-1][1]) if priced else None
    liqs = [l for _, _, l in s if l is not None]
    gone = any(p is None for _, p, _ in s[1:]) and bool(s)
    if liq0 is None and liqs:
        liq0 = liqs[0]
    out["liq0"] = liq0
    out["liq_min"] = min(liqs) if liqs else None
    out["rugged"] = bool(liq0 and liqs and min(liqs) < liq0 * 0.2) or gone
    return out


def _stage(c: dict, scan, red, verdict, signal, pos) -> tuple[str, str | None]:
    if pos:
        return "traded", pos["exit_reason"] or pos["status"]
    if signal and signal["status"] in ("skipped", "failed", "missed"):
        return "executor_skip", signal["reason"] or signal["status"]
    if verdict and not verdict["pass"]:
        return "analyst_reject", verdict["reason"]
    if red:
        return "audit_reject", f"layer {red['layer']} · {red['value_text']}"
    if scan and scan["verdict"] == "kill":
        return "scanner_kill", scan["reason"]
    if scan and scan["verdict"] == "drop":
        return "scanner_drop", scan["reason"]
    if c.get("state") == "waiting":
        return "waiting", None
    return "passed", None


def _load(js: str | None):
    try:
        return json.loads(js) if js else None
    except ValueError:
        return None


def export(hours: float = 24.0) -> dict:
    now = time.time()
    since = now - hours * 3600
    coins = db.query("SELECT * FROM coins WHERE first_seen>=? ORDER BY first_seen", (since,))
    out_coins = []
    for c in coins:
        m = c["mint"]
        scan = db.one("SELECT * FROM scans WHERE mint=? ORDER BY id DESC LIMIT 1", (m,))
        layers = db.query("SELECT layer, name, value_text, pass, ts FROM audits WHERE mint=? AND run_id=("
                          "SELECT run_id FROM audits WHERE mint=? ORDER BY id DESC LIMIT 1) ORDER BY layer", (m, m))
        red = next((l for l in layers if not l["pass"]), None)
        verdict = db.one("SELECT * FROM verdicts WHERE mint=? ORDER BY id DESC LIMIT 1", (m,))
        signal = db.one("SELECT * FROM signals WHERE mint=? ORDER BY id DESC LIMIT 1", (m,))
        pos = db.one("SELECT * FROM positions WHERE mint=? ORDER BY id DESC LIMIT 1", (m,))
        stage, reason = _stage(c, scan, red, verdict, signal, pos)
        t0 = (pos or {}).get("opened_at") or (scan or {}).get("ts") or c["first_seen"]
        snap = _load((scan or {}).get("snapshot")) or {}
        p0 = (pos or {}).get("entry_price") or snap.get("price")
        row = {
            "mint": m, "symbol": c.get("symbol"), "name": c.get("name"), "first_seen": c["first_seen"],
            "decided_at": t0, "stage": stage, "reason": reason, "link": links.coin_url(m, "solana"),
            "scan": None if not scan else {"verdict": scan["verdict"], "reason": scan["reason"],
                                           "liquidity_usd": scan["liquidity_usd"], "top10_pct": scan["top10_pct"],
                                           "h1_change_pct": scan["h1_change_pct"], "age_min": scan["age_min"],
                                           "seen": snap},
            "audit": [{"n": l["layer"], "name": l["name"], "value": l["value_text"], "pass": bool(l["pass"])}
                      for l in layers] or None,
            "holders": c.get("holders"),
            "analyst": None if not verdict else {
                "alive_6h": verdict["alive_6h"], "organic": verdict["organic"], "flush": verdict["flush"],
                "chase": verdict["chase"], "score": verdict["score"], "pass": bool(verdict["pass"]),
                "reason": verdict["reason"], "model": verdict["model"], "seen": _load(verdict.get("snapshot"))},
            "executor": None if not signal else {"status": signal["status"], "reason": signal["reason"],
                                                 "size_usd": signal["size_usd"], "price": signal["price"]},
            "trade": None if not pos else {
                "mode": pos["mode"], "venue": pos["venue"], "entry": pos["entry_price"], "exit": pos["exit_price"],
                "size_usd": pos["size_usd"], "pnl_usd": pos["pnl_usd"], "fees_usd": pos["fees_usd"],
                "pct": round(pos["pnl_usd"] / pos["size_usd"] * 100, 2) if pos["pnl_usd"] is not None else None,
                "exit_reason": pos["exit_reason"], "peak": pos["peak_price"], "status": pos["status"],
                "held_min": round(((pos["closed_at"] or now) - pos["opened_at"]) / 60, 1)},
            "outcome": outcome(m, t0, p0, (scan or {}).get("liquidity_usd")),
        }
        out_coins.append(row)
    return {"exported_at": now, "hours": hours, "from": since, "to": now,
            "desk": {"mode": config.get("desk.mode"), "venue": config.get("desk.venue"),
                     "analyst_mode": config.get("analyst.mode")},
            "settings": {k: (mask_url(v) if k == "desk.rpc_url" else v) for k, v in config.all_values().items()},
            "summary": summarize(out_coins),
            "coins": out_coins,
            "change_log": [{**r, "old": mask_url(r["old"]), "new": mask_url(r["new"])} if r["key"] == "desk.rpc_url" else r
                           for r in db.query("SELECT * FROM change_log WHERE ts>=? ORDER BY ts", (since,))],
            "how_to_read": ("outcome.*_pct = price change from the decision price (entry for trades). "
                            "max_up/max_down = best/worst within 6h. rugged = liquidity fell >80% or the pair vanished. "
                            "A stage with high max_up_pct is where the filters may be too strict; a 'traded' row that "
                            "rugged is where they were too loose.")}


def summarize(rows: list[dict]) -> dict:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["stage"], []).append(r)

    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(statistics.median(xs), 2) if xs else None

    stages = {}
    for st, rs in by.items():
        o = [r["outcome"] for r in rs]
        reasons: dict[str, int] = {}
        for r in rs:
            key = (r["reason"] or "-").split(" ")[0] if not (r["reason"] or "").startswith("top 10") else "top 10"
            reasons[key] = reasons.get(key, 0) + 1
        stages[st] = {"n": len(rs), "reasons": reasons,
                      "median_1h_pct": med([x["at_1h_pct"] for x in o]),
                      "median_max_up_pct": med([x["max_up_pct"] for x in o]),
                      "doubled_within_6h": sum(1 for x in o if (x["max_up_pct"] or 0) >= 100),
                      "rugged": sum(1 for x in o if x["rugged"])}
    trades = [r["trade"] for r in rows if r["trade"] and r["trade"]["status"] == "closed"]
    return {"coins": len(rows), "stages": stages,
            "trades": {"closed": len(trades), "wins": sum(1 for t in trades if (t["pnl_usd"] or 0) > 0),
                       "pnl_usd": round(sum(t["pnl_usd"] or 0 for t in trades), 4),
                       "fees_usd": round(sum(t["fees_usd"] or 0 for t in trades), 4),
                       "by_exit": {k: sum(1 for t in trades if t["exit_reason"] == k)
                                   for k in {t["exit_reason"] for t in trades}}}}
