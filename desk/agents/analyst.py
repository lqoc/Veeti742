"""AGENT 3 · ANALYST. Builds a numbers-only state, asks 4 closed questions (Jev) or computes them (rules),
and code applies the gate. Never decides itself."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time

import httpx

from .. import bus, config, db, notifier, sources, vault
from .base import Agent, iso, short

JEV_URL = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {
    "alive_6h": {"type": "noul", "instructions": "This coin is still actively traded 6 hours from now"},
    "organic": {"type": "noul", "instructions": "Most buyers in the last 30 minutes are separate real wallets"},
    "flush": {"type": "noul", "instructions": "Price just flushed down and buyers are absorbing it"},
    "chase": {"type": "noul", "instructions": "This entry is chasing a green candle that already ran"},
}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def fmt(v, nd=0):
    if v is None:
        return "n/a"
    return f"{v:,.{nd}f}"


def build_state(scan: dict, audit: dict, v: dict) -> str:
    ex = (audit or {}).get("extra") or {}
    age = int((time.time() - (scan.get("_migrated_at") or time.time())) / 60) if scan.get("_migrated_at") \
        else scan.get("ageMin")
    s = (f"Coin {scan.get('name') or scan.get('symbol') or 'unknown'} on Solana, graduated {age} min ago. "
         f"Liquidity ${fmt(v.get('liq'))}. Market cap ${fmt(v.get('mcap'))}.\n"
         f"Last 5 min: {fmt(v.get('b5'))} buys, {fmt(v.get('s5'))} sells, volume ${fmt(v.get('v5'))}, "
         f"price {fmt(v.get('p5'), 1)}%.\n"
         f"Last 1h: {fmt(v.get('b1'))} buys, {fmt(v.get('s1'))} sells, volume ${fmt(v.get('v1'))}, "
         f"price {fmt(v.get('p1'), 1)}%.\n"
         f"Top 10 hold {fmt(ex.get('top10'), 1)}%. Dev holds {fmt(ex.get('dev_pct'), 1)}%, sold 0. "
         f"LP {ex.get('lp') or 'n/a'}. Bundled {ex.get('bundled') if ex.get('bundled') is not None else 'n/a'}. "
         f"Delegations {ex.get('delegations') or 'n/a'}.")
    return s[:3000]


def rules_answers(v: dict, bundled: int | None) -> dict:
    g = config.get
    if g("analyst.rules_window") == "m5":   # sniper: a 1-3 minute old pool has no real hour yet
        alive = clamp((v.get("v5") or 0) / g("analyst.rules_alive_volume_m5"))
        organic = clamp((v.get("b5") or 0) / g("analyst.rules_organic_buys_m5"))
    else:
        alive = clamp((v.get("v1") or 0) / g("analyst.rules_alive_volume_h1"))
        organic = clamp((v.get("b1") or 0) / g("analyst.rules_organic_buys_h1"))
    if bundled is not None and bundled >= g("analyst.rules_bundled_from"):
        organic *= g("analyst.rules_bundled_penalty")
    p5 = v.get("p5") or 0
    if p5 <= -g("analyst.rules_flush_drop_m5_pct") and (v.get("b5") or 0) >= (v.get("s5") or 0):
        flush = 1.0
    elif p5 < 0:
        flush = 0.5
    else:
        flush = 0.0
    if g("analyst.rules_window") == "m5":
        chase = clamp((v.get("p5") or 0) / g("analyst.rules_chase_m5_pct"))
    else:
        chase = clamp((v.get("p1") or 0) / g("analyst.rules_chase_h1_pct"))
    return {"alive_6h": round(alive, 4), "organic": round(organic, 4), "flush": flush, "chase": round(chase, 4)}


def gate(a: dict) -> tuple[int, list[str]]:
    """Code-only gate. Returns (score, failed rules in order)."""
    score = round(100 * (a["alive_6h"] + a["organic"] + a["flush"] + (1 - a["chase"])) / 4)
    failed = []
    if a["alive_6h"] < config.get("analyst.min_alive_6h"):
        failed.append(f"alive {a['alive_6h']:.2f}")
    if a["organic"] < config.get("analyst.min_organic"):
        failed.append(f"organic {a['organic']:.2f}")
    if a["chase"] > config.get("analyst.max_chase"):
        failed.append(f"chase {a['chase']:.2f}")
    if score < config.get("analyst.min_score"):
        failed.append(f"score {score}")
    return score, failed


class JevError(Exception):
    pass


async def ask_jev(state: str) -> tuple[dict, str, int, dict]:
    key = vault.get("TYPESAFE_API_KEY")
    if not key:
        raise JevError("no answer")
    body = {"model": "jev-latest", "state": state, "questions": QUESTIONS}
    started = time.time()
    attempt = 0
    while True:
        try:
            r = await sources.client().post(JEV_URL, json=body, headers={"Authorization": f"Bearer {key}"},
                                            timeout=config.get("analyst.timeout_s"))
        except httpx.HTTPError:
            r = None
        if r is not None and r.status_code == 429:
            if time.time() - started > 60:
                raise JevError("no answer")
            await asyncio.sleep(5)
            continue
        if r is not None and r.status_code == 200:
            data = r.json()
            ans = {}
            for name in QUESTIONS:
                val = ((data.get("answers") or {}).get(name) or {}).get("noul")
                if not isinstance(val, (int, float)) or not 0 <= val <= 1:
                    raise JevError("bad answer|" + r.text[:500])
                ans[name] = float(val)
            usage = data.get("usage") or {}
            return ans, data.get("model") or "jev", int(usage.get("input_tokens") or usage.get("inputTokens") or 0), usage
        attempt += 1
        if attempt > 1:
            raise JevError("no answer")
        await asyncio.sleep(2)


class Analyst(Agent):
    name = "analyst"

    def __init__(self) -> None:
        super().__init__()
        self.last: dict | None = None
        self.scoring: str | None = None

    async def run(self) -> None:
        while True:
            self.beat()
            if self.paused:
                await asyncio.sleep(2)
                continue
            try:
                msg = await asyncio.wait_for(bus.q("analyst").get(), timeout=10)
            except asyncio.TimeoutError:
                continue
            try:
                await self.handle(msg["scan"], msg["audit"])
            except Exception as e:
                self.log.info("%s analyst crashed: %s", msg.get("scan", {}).get("mint"), e)

    async def handle(self, scan: dict, audit: dict) -> dict | None:
        mint = scan["mint"]
        if db.one("SELECT 1 FROM verdicts WHERE mint=? AND ts>=?", (mint, self.night())):
            self.log.info("%s already scored tonight, not re-asking", mint)
            return None
        self.scoring = mint
        mode = config.get("analyst.mode")
        try:
            pairs = await sources.dex_tokens([mint])
        except sources.SourceError:
            pairs = {}
        v = sources.pair_view(pairs.get(mint))
        coin = db.one("SELECT migrated_at FROM coins WHERE mint=?", (mint,)) or {}
        scan = dict(scan, _migrated_at=coin.get("migrated_at"))
        state = build_state(scan, audit, v)
        model, tokens, failed, answers, score = mode, 0, [], None, 0
        if v.get("price") is None:
            failed = ["no data"]
        elif mode == "rules":
            answers = rules_answers(v, ((audit or {}).get("extra") or {}).get("bundled"))
            model = "rules"
            score, failed = gate(answers)
        else:
            try:
                answers, model, tokens, _usage = await ask_jev(state)
                score, failed = gate(answers)
            except JevError as e:
                msg = str(e)
                failed = ["bad answer" if msg.startswith("bad answer") else "no answer"]
                if "|" in msg:
                    self.log.info("%s raw jev response: %s", mint, msg.split("|", 1)[1])
        passed = not failed
        db.execute("UPDATE coins SET price_at_analyst=? WHERE mint=?", (v.get("price"), mint))
        out = {"agent": "analyst", "mint": mint, "answers": answers, "score": score if answers else 0,
               "passed": passed, "failed": failed, "model": model, "inputTokens": tokens}
        db.execute("INSERT INTO verdicts(mint, ts, alive_6h, organic, flush, chase, score, pass, reason, input_tokens, model, "
                   "snapshot) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (mint, time.time(), *(answers[k] if answers else None for k in QUESTIONS), out["score"],
                    int(passed), failed[0] if failed else None, tokens, model, json.dumps(v)))
        h = hashlib.sha256(state.encode()).hexdigest()[:12]
        self.log.info("%s state=%s %s score=%s %s tokens=%s", mint, h, answers, out["score"],
                      "PASS" if passed else f"REJECT {failed}", tokens)
        self.last = {**out, "name": scan.get("name"), "symbol": scan.get("symbol")}
        self.scoring = None
        bus.publish("verdict", self.last)
        if passed:
            await bus.send("executor", {"scan": scan, "audit": audit, "analyst": out, "price": v.get("price")})
        else:
            who = "Analyst (rules)" if model == "rules" else "Analyst"
            name = scan.get("name") or scan.get("symbol") or short(mint)
            db.execute("INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value) VALUES(?,?,?,?,?,?,?)",
                       (time.time(), "rejected", mint, "analyst", f"Rejected {name}", f"{who} · {failed[0]}",
                        "Skipped"))
            bus.publish("activity", {"mint": mint})
            if config.get("desk.push_rejects"):
                await notifier.push(f"Rejected {name} · {who} · {failed[0]}", mint=mint)
        return out

    def card(self) -> dict:
        last = self.last
        if not last:
            r = db.one("SELECT v.*, c.name, c.symbol FROM verdicts v LEFT JOIN coins c ON c.mint=v.mint "
                       "ORDER BY v.id DESC LIMIT 1")
            if r:
                last = {"mint": r["mint"], "score": r["score"], "passed": bool(r["pass"]), "name": r["name"],
                        "symbol": r["symbol"], "model": r["model"], "failed": [r["reason"]] if r["reason"] else []}
        return {"name": "analyst", "title": "Analyst", "status": "Scoring", "scoring": self.scoring,
                "last": last, "green_from": config.get("analyst.min_score"), "mode": config.get("analyst.mode")}

    def decisions(self, limit: int = 20) -> list[dict]:
        rows = db.query("SELECT v.*, c.name, c.symbol FROM verdicts v LEFT JOIN coins c ON c.mint=v.mint "
                        "ORDER BY v.id DESC LIMIT ?", (limit,))
        return [{"ts": r["ts"], "mint": r["mint"], "name": r["name"], "symbol": r["symbol"],
                 "verdict": "pass" if r["pass"] else "reject",
                 "reason": f"score {r['score']}" if r["pass"] else r["reason"]} for r in rows]
