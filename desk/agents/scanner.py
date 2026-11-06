"""AGENT 1 · SCANNER. Counts and cuts every coin that graduates off pump.fun onto PumpSwap."""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque

import websockets

from .. import bus, config, db, fields, sources
from .base import Agent, iso, money

WS_URL = "wss://pumpportal.fun/api/data"


def norm(s: str | None) -> str:
    """lowercase, strip spaces, digits, emoji and anything that isn't a letter."""
    return re.sub(r"[^a-z]", "", (s or "").lower())


def is_copycat(name: str | None, symbol: str | None, runners: list[tuple[str, str, str]]) -> str | None:
    mine = {x for x in (norm(name), norm(symbol)) if len(x) >= 3}
    for rmint, rname, rsym in runners:
        for theirs in {x for x in (norm(rname), norm(rsym)) if len(x) >= 3}:
            for m in mine:
                if m == theirs or m in theirs or theirs in m:
                    return rname or rsym
    return None


def record_prices(pairs: dict[str, dict]) -> None:
    now = time.time()
    rows = []
    for mint, p in pairs.items():
        px = fields.num("dexscreener", "price_usd", p)
        if px:
            rows.append((mint, now, px, fields.num("dexscreener", "volume_m5", p, 0)))
    if rows:
        db.executemany("INSERT INTO prices(mint, ts, price, volume_m5) VALUES(?,?,?,?)", rows)


class Scanner(Agent):
    name = "scanner"

    def __init__(self) -> None:
        super().__init__()
        self.reconnects: deque[float] = deque(maxlen=500)      # real disconnects (errors)
        self.silent_reconnects: deque[float] = deque(maxlen=500)  # no message for ws_silent_s
        self.last_msg = 0.0
        self.connected = False

    # ------------------------------------------------------------ websocket
    async def run(self) -> None:
        await asyncio.gather(self.ws_loop(), self.poll_loop())

    async def ws_loop(self) -> None:
        backoff = 1
        while True:
            started = time.time()
            try:
                async with websockets.connect(WS_URL, open_timeout=20, ping_interval=20) as ws:
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    self.connected = True
                    self.last_msg = time.time()
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=config.get("scanner.ws_silent_s"))
                        self.last_msg = time.time()
                        self.beat()
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        if fields.get("pumpportal", "mint", msg):
                            await self.on_migration(msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected = False
                if time.time() - started > 60:
                    backoff = 1
                (self.silent_reconnects if isinstance(e, asyncio.TimeoutError) else self.reconnects).append(time.time())
                db.event("ws_reconnect", f"{e.__class__.__name__}: {str(e)[:80]}")
                self.log.info("ws reconnect in %ss (%s)", backoff, e.__class__.__name__)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def reconnects_last_hour(self) -> int:
        return sum(1 for t in self.reconnects if time.time() - t < 3600)

    async def on_migration(self, msg: dict) -> None:
        mint = fields.get("pumpportal", "mint", msg)
        if self.paused:
            self.log.info("%s ignored (paused)", mint)
            return
        if db.one("SELECT 1 FROM coins WHERE mint=?", (mint,)):
            return
        now = time.time()
        db.execute("INSERT INTO coins(mint, signature, migrated_at, creator, state, first_seen) VALUES(?,?,?,?,?,?)",
                   (mint, fields.get("pumpportal", "signature", msg), now,
                    fields.get("pumpportal", "creator", msg), "waiting", now))
        bus.publish("graduation", {"mint": mint, "ts": iso(now)})
        asyncio.create_task(self.enrich(mint))

    async def enrich(self, mint: str) -> None:
        """Creator from the message, else RugCheck; store the creator's balance NOW for 'sold 0'."""
        creator = db.scalar("SELECT creator FROM coins WHERE mint=?", (mint,))
        for attempt in range(6):
            if creator:
                break
            try:
                rep = await sources.rug_report(mint)
                creator = fields.get("rugcheck_report", "creator", rep)
                if creator:
                    db.execute("UPDATE coins SET creator=? WHERE mint=?", (creator, mint))
            except sources.SourceError:
                pass
            if not creator:
                await asyncio.sleep(10 * (attempt + 1))
        if not creator:
            self.log.info("%s creator unknown", mint)
            return
        try:
            bal = await sources.owner_balance(creator, mint)
            db.execute("UPDATE coins SET creator_balance_at_migration=? WHERE mint=?", (str(bal), mint))
        except sources.SourceError as e:
            self.log.info("%s creator balance failed: %s", mint, e)

    # ------------------------------------------------------------ DexScreener poll
    def runners(self, exclude: str) -> list[tuple[str, str, str]]:
        day0 = time.time() - 86400
        rows = db.query("SELECT mint, name, symbol FROM coins WHERE first_seen>=? AND h24_max>=? AND mint<>?",
                        (day0, config.get("scanner.runner_h24_pct"), exclude))
        return [(r["mint"], r["name"], r["symbol"]) for r in rows]

    async def poll_loop(self) -> None:
        while True:
            self.beat()
            try:
                await self.poll_once()
            except sources.RateLimited:
                self.log.info("dexscreener 429, waiting 30s, room kept")
                await asyncio.sleep(30)
                continue
            except Exception as e:
                self.log.info("poll error %s: %s", e.__class__.__name__, e)
            await asyncio.sleep(config.get("scanner.poll_s"))

    async def poll_once(self) -> None:
        room = db.query("SELECT * FROM coins WHERE state='waiting'")
        if not room:
            return
        pairs = await sources.dex_tokens([c["mint"] for c in room])
        record_prices(pairs)
        now = time.time()
        for c in room:
            mint = c["mint"]
            p = pairs.get(mint)
            age = (now - c["migrated_at"]) / 60
            if p is None:
                if age >= config.get("scanner.max_wait_min"):
                    self.decide(c, None, age, "drop", "never qualified")
                continue
            v = sources.pair_view(p)
            db.execute("UPDATE coins SET name=COALESCE(?,name), symbol=COALESCE(?,symbol), pool=COALESCE(?,pool), "
                       "image=COALESCE(?,image), h24_max=MAX(COALESCE(h24_max,-1e9), ?) WHERE mint=?",
                       (v["name"], v["symbol"], v["pair"], v["image"], v["p24"] if v["p24"] is not None else -1e9,
                        mint))
            c.update({"name": v["name"] or c["name"], "symbol": v["symbol"] or c["symbol"], "pool": v["pair"]})
            if age < config.get("scanner.min_pool_age_min"):
                continue
            await self.evaluate(c, v, age)

    async def evaluate(self, c: dict, v: dict, age: float) -> None:
        mint = c["mint"]
        liq, h1 = v["liq"], v["p1"]
        if liq is None or liq < config.get("scanner.min_liquidity_usd"):
            return self.decide(c, v, age, "kill", f"liq {money(liq or 0)}")
        try:
            supply, dec = await sources.token_supply(mint)
            top10, _how = await sources.top10_pct(mint, v["pair"], supply)
            db.execute("UPDATE coins SET decimals=? WHERE mint=?", (dec, mint))
        except sources.SourceError as e:
            self.log.info("%s top10 not readable yet (%s)", mint, e)
            if age >= config.get("scanner.max_wait_min"):
                self.decide(c, v, age, "drop", "never qualified")
            return
        if top10 > config.get("scanner.max_top10_pct"):
            return self.decide(c, v, age, "kill", f"top 10 hold {top10:.0f}%", top10)
        if h1 is not None and h1 > config.get("scanner.max_h1_change_pct"):
            return self.decide(c, v, age, "kill", f"+{h1:.0f}% in 1h", top10)
        cc = is_copycat(c.get("name"), c.get("symbol"), self.runners(mint))
        if cc:
            return self.decide(c, v, age, "kill", f"copycat of {cc}", top10)
        self.decide(c, v, age, "pass", None, top10)

    def decide(self, c: dict, v: dict | None, age: float, verdict: str, reason: str | None,
               top10: float | None = None) -> None:
        mint = c["mint"]
        now = time.time()
        state = {"pass": "passed", "kill": "killed", "drop": "dropped"}[verdict]
        db.execute("UPDATE coins SET state=?, liq_at_scan=? WHERE mint=?", (state, (v or {}).get("liq"), mint))
        out = {"agent": "scanner", "mint": mint, "name": c.get("name"), "symbol": c.get("symbol"),
               "pool": (v or {}).get("pair") or c.get("pool"), "liquidityUsd": (v or {}).get("liq"),
               "top10Pct": top10, "h1ChangePct": (v or {}).get("p1"), "ageMin": int(age),
               "verdict": "pass" if verdict == "pass" else "kill", "reason": reason, "ts": iso(now)}
        db.execute("INSERT INTO scans(mint, ts, liquidity_usd, top10_pct, h1_change_pct, age_min, verdict, reason) "
                   "VALUES(?,?,?,?,?,?,?,?)", (mint, now, out["liquidityUsd"], top10, out["h1ChangePct"],
                                              age, verdict, reason))
        self.log.info("%s %s %s %s liq=%s top10=%s h1=%s age=%.0f", out["ts"], mint, verdict, reason,
                      out["liquidityUsd"], top10, out["h1ChangePct"], age)
        bus.publish("scan", out)
        if verdict == "pass":
            bus.q("auditor").put_nowait(out)

    # ------------------------------------------------------------ app
    def card(self) -> dict:
        ns = self.night()
        pools = db.scalar("SELECT COUNT(*) FROM coins WHERE first_seen>=?", (ns,), 0)
        waiting = db.scalar("SELECT COUNT(*) FROM coins WHERE state='waiting'", (), 0)
        feed = db.query("SELECT s.mint, s.verdict, s.reason, s.liquidity_usd, s.ts, c.name, c.symbol FROM scans s "
                        "LEFT JOIN coins c ON c.mint=s.mint ORDER BY s.id DESC LIMIT 6")
        return {"name": "scanner", "title": "Scanner", "status": f"{pools} pools", "pools": pools,
                "waiting": waiting, "connected": self.connected, "feed": feed}

    def decisions(self, limit: int = 20) -> list[dict]:
        rows = db.query("SELECT s.*, c.name, c.symbol FROM scans s LEFT JOIN coins c ON c.mint=s.mint "
                        "ORDER BY s.id DESC LIMIT ?", (limit,))
        return [{"ts": r["ts"], "mint": r["mint"], "name": r["name"], "symbol": r["symbol"],
                 "verdict": r["verdict"], "reason": r["reason"] or "→ auditor"} for r in rows]
