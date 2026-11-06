"""AGENT 4 · EXECUTOR. Never picks a coin. Does the arithmetic, then buys from your wallet
(venue "wallet", LIVE) or simulates the fill (PAPER), or sends the FOMO button (venue "fomo")."""
from __future__ import annotations

import asyncio
import time

from .. import book, bus, config, db, fees, links, notifier, sources, trader, wallet
from .base import Agent, iso, money, short

TIERS = (52, 60, 70, 80, 90)


def tier_usd(score: int) -> float:
    amt = 0.0
    for t in TIERS:
        if score >= t:
            amt = config.get(f"executor.tier_usd_{t}")
    return amt


def size_for(score: int, liq: float | None, stack: float, free: float) -> dict:
    """Pure sizing. Returns {size, skip, caps:[...], raw}."""
    caps = []
    if config.get("executor.size_mode") == "tiers":
        raw = tier_usd(score)
        if raw <= 0:
            return {"size": 0, "skip": f"score {score} under tiers", "caps": caps, "raw": 0}
        size = raw
        if config.get("desk.venue") == "wallet" and size > config.get("wallet.max_trade_usd"):
            size = config.get("wallet.max_trade_usd")
            caps.append("hard cap")
    else:
        raw = stack * config.get("executor.stack_pct") / 100
        size = raw
    pool_cap = (liq or 0) * config.get("executor.max_pool_pct") / 100
    if size > pool_cap:
        size = pool_cap
        caps.append("pool")
    if config.get("executor.size_mode") == "pct" and size < config.get("executor.min_ticket_usd"):
        if config.get("executor.below_min") == "bump":
            size = config.get("executor.min_ticket_usd")
            caps.append("bumped")
        else:
            return {"size": 0, "skip": f"under ${config.get('executor.min_ticket_usd'):.2f}", "caps": caps,
                    "raw": raw}
    size = round(size, 2)
    if size <= 0:
        return {"size": 0, "skip": "no liquidity", "caps": caps, "raw": raw}
    if size > free + 1e-9:
        why = "no free stack"
        if book.live_wallet():
            why = f"no free stack (keeps last ${config.get('executor.usdc_reserve_usd'):.2f} USDC)"
        return {"size": 0, "skip": why, "caps": caps, "raw": raw}
    return {"size": size, "skip": None, "caps": caps, "raw": raw}


class Executor(Agent):
    name = "executor"

    def __init__(self) -> None:
        super().__init__()
        self.executing: dict | None = None

    async def run(self) -> None:
        while True:
            self.beat()
            if self.paused:
                await asyncio.sleep(2)
                continue
            try:
                msg = await asyncio.wait_for(bus.q("executor").get(), timeout=10)
            except asyncio.TimeoutError:
                continue
            try:
                await self.handle(msg)
            except Exception as e:
                self.log.info("%s executor crashed: %s", msg.get("scan", {}).get("mint"), e)

    def skip(self, mint: str, name: str, reason: str, price=None, size=0.0, score=None) -> dict:
        sid = db.execute("INSERT INTO signals(mint, ts, price, size_usd, stack_pct, link, mode, status, reason, score) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)", (mint, time.time(), price, size, None,
                                                         links.coin_url(mint, "solana"), book.mode(), "skipped",
                                                         reason, score))
        db.execute("INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value, ref_id) VALUES(?,?,?,?,?,?,?,?)",
                   (time.time(), "rejected", mint, "executor", f"Rejected {name}", f"Executor · {reason}", "Skipped",
                    sid))
        self.log.info("%s skip %s", mint, reason)
        bus.publish("signal", {"mint": mint, "status": "skipped", "reason": reason})
        bus.publish("activity", {"mint": mint})
        return {"agent": "executor", "mint": mint, "status": "skipped", "reason": reason}

    async def handle(self, msg: dict) -> dict | None:
        scan, analyst = msg["scan"], msg["analyst"]
        mint = scan["mint"]
        name = scan.get("name") or scan.get("symbol") or short(mint)
        score = int(analyst.get("score") or 0)
        ns = self.night()
        if db.one("SELECT 1 FROM signals WHERE mint=? AND ts>=? AND status<>'skipped'", (mint, ns)):
            self.log.info("%s second analyst pass ignored", mint)
            return None
        if db.one("SELECT 1 FROM skips WHERE mint=? AND until>?", (mint, time.time())):
            return None
        if db.one("SELECT 1 FROM positions WHERE mint=? AND status IN ('open','exiting')", (mint,)):
            return None
        if len(book.open_positions()) >= config.get("executor.max_positions"):
            return self.skip(mint, name, f"{config.get('executor.max_positions')} positions open", score=score)
        lim = config.get("risk.daily_loss_limit_usd")
        if lim and book.today_loss() >= lim:
            return self.skip(mint, name, f"daily loss limit {money(lim)}", score=score)
        live_wallet = book.live_wallet()
        if live_wallet:
            if not wallet.pubkey():
                return self.skip(mint, name, "no wallet imported", score=score)
            await book.refresh_wallet()
            if (book.wallet_cached().get("sol") or 0) < config.get("wallet.min_sol_reserve"):
                return self.skip(mint, name, "low SOL for fees", score=score)
        # fresh read right before the push
        try:
            v = sources.pair_view((await sources.dex_tokens([mint])).get(mint))
        except sources.SourceError:
            v = {}
        price = v.get("price")
        if not price:
            return self.skip(mint, name, "no price", score=score)
        stack = book.stack_value()
        sz = size_for(score, v.get("liq"), stack, book.free_cash())
        self.log.info("%s stack=%.2f raw=%.2f caps=%s final=%.2f mode=%s", mint, stack, sz["raw"], sz["caps"],
                      sz["size"], book.mode())
        if sz["skip"]:
            return self.skip(mint, name, sz["skip"], price, score=score)
        liq_scan = scan.get("liquidityUsd")
        if liq_scan and v.get("liq") is not None and \
                v["liq"] < liq_scan * (1 - config.get("executor.recheck_liq_drop_pct") / 100):
            return self.skip(mint, name, "liquidity dropping", price, score=score)
        pa = msg.get("price")
        if pa and price > pa * (1 + config.get("executor.recheck_price_up_pct") / 100):
            return self.skip(mint, name, "ran before the push", price, score=score)
        size = sz["size"]
        pct = round(size / stack * 100, 1) if stack else 0
        link = links.coin_url(mint, "solana")
        sid = db.execute("INSERT INTO signals(mint, ts, price, size_usd, stack_pct, link, mode, status, score) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (mint, time.time(), price, size, pct, link, book.mode(), "sent",
                                                       score))
        self.executing = {"mint": mint, "name": name, "size": size, "pct": pct, "started": time.time(),
                          "status": "executing", "id": sid}
        bus.publish("signal", {**self.executing, "link": link})
        tp = config.get("risk.take_profit_pct")
        detail = (f"Auditor {msg['audit'].get('layersChecked', 0)}/8 · Analyst {score} · "
                  f"stop -{config.get('risk.stop_pct'):g}%" + (f" · target +{tp:g}%" if tp > 0 else "") +
                  f" · trail {config.get('risk.trail_pct'):g}%")
        venue = config.get("desk.venue")
        if book.mode() == "PAPER":
            await asyncio.sleep(1.2)
            fee = fees.side(size)
            self.open_position(sid, mint, scan, price, size, fee, score, venue)
            book.paper_cash_add(-(size + fee))
            verb = "Bought" if venue == "wallet" else "Buy"
            await notifier.push(f"{verb} {name} · ${size:.2f} · {pct:g}% of stack\n{detail}", mint=mint)
        elif venue == "wallet":
            try:
                t = await trader.buy(mint, size)
            except Exception as e:  # network, wallet
                t = trader.Trade(False, f"{e.__class__.__name__}: {str(e)[:80]}")
            if not t.ok:
                db.execute("UPDATE signals SET status='failed', reason=? WHERE id=?", (t.reason, sid))
                self.executing = None
                bus.publish("signal", {"mint": mint, "status": "failed", "reason": t.reason})
                db.execute("INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value, ref_id) "
                           "VALUES(?,?,?,?,?,?,?,?)", (time.time(), "rejected", mint, "executor", f"Rejected {name}",
                                                       f"Executor · buy failed: {t.reason}", "Skipped", sid))
                await notifier.push(f"Buy failed {name} · {t.reason}", mint=mint)
                self.log.info("%s buy failed %s sig=%s", mint, t.reason, t.sig)
                return {"agent": "executor", "mint": mint, "status": "skipped", "reason": t.reason}
            self.open_position(sid, mint, scan, t.price, t.usd, t.sol_cost_usd, score, venue,
                               tokens_raw=t.tokens_raw, decimals=t.decimals, sig=t.sig)
            size = round(t.usd, 2)
            await book.refresh_wallet()
            await notifier.push(f"Bought {name} · ${size:.2f} · {pct:g}% of stack\n{detail}", mint=mint)
        else:  # LIVE through FOMO: you tap Buy on FOMO, then [Bought]
            res = await notifier.push(f"Buy {name} · ${size:.2f} · {pct:g}% of stack\n{detail}", mint=mint)
            asyncio.create_task(self.confirm_window(sid))
            self.log.info("%s signal sent %s", mint, res.get("url"))
        out = {"agent": "executor", "mint": mint, "signalPrice": price, "sizeUsd": size, "stackPct": pct,
               "link": link, "mode": book.mode(), "status": "bought" if book.mode() == "PAPER" or venue == "wallet"
               else "sent", "reason": None}
        self.log.info("%s %s size=%.2f link=%s", mint, out["status"], size, link)
        return out

    def open_position(self, sid, mint, scan, price, size, fee, score, venue, tokens_raw=None, decimals=None,
                      sig=None) -> int:
        stop = config.get("risk.stop_pct")
        coin = db.one("SELECT name, symbol FROM coins WHERE mint=?", (mint,)) or {}
        pid = db.execute(
            "INSERT INTO positions(mint, opened_at, entry_price, size_usd, peak_price, stop_price, trail_active, "
            "fees_usd, mode, name, symbol, stop_pct, status, tokens_raw, decimals, buy_sig, last_price, score, venue) "
            "VALUES(?,?,?,?,?,?,0,?,?,?,?,?,'open',?,?,?,?,?,?)",
            (mint, time.time(), price, size, price, price * (1 - stop / 100), fee, book.mode(),
             coin.get("name") or scan.get("name"), coin.get("symbol") or scan.get("symbol"), stop,
             str(tokens_raw) if tokens_raw is not None else None, decimals, sig, price, score, venue))
        db.execute("UPDATE signals SET status='bought' WHERE id=?", (sid,))
        name = coin.get("name") or scan.get("name") or short(mint)
        stack = book.stack_value() or 1
        db.execute("INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value, value2, ref_id) "
                   "VALUES(?,?,?,?,?,?,?,?,?)",
                   (time.time(), "bought", mint, "executor", f"Bought {name}",
                    f"Executor · {time.strftime('%H:%M', time.gmtime())}",
                    f"${size:.2f}", f"{size / stack * 100:.0f}% of stack", pid))
        self.executing = {"mint": mint, "name": name, "size": size, "status": "filled", "started": time.time(),
                          "id": sid, "pct": round(size / stack * 100, 1)}
        bus.publish("fill", {"mint": mint, "position": pid, "price": price, "size": size, "sig": sig})
        bus.publish("activity", {"mint": mint})
        return pid

    async def confirm_window(self, sid: int) -> None:
        await asyncio.sleep(config.get("executor.confirm_window_min") * 60)
        s = db.one("SELECT status FROM signals WHERE id=?", (sid,))
        if s and s["status"] == "sent":
            db.execute("UPDATE signals SET status='missed' WHERE id=?", (sid,))
            self.executing = None
            bus.publish("signal", {"id": sid, "status": "missed"})

    def tap_bought(self, sid: int) -> int | None:
        s = db.one("SELECT * FROM signals WHERE id=?", (sid,))
        if not s or s["status"] != "sent":
            return None
        return self.open_position(sid, s["mint"], {}, s["price"], s["size_usd"], 0.0, s["score"], "fomo")

    def tap_skip(self, sid: int) -> None:
        s = db.one("SELECT * FROM signals WHERE id=?", (sid,))
        if s:
            db.execute("UPDATE signals SET status='skipped', reason='you skipped' WHERE id=?", (sid,))
            db.execute("INSERT INTO skips(mint, until) VALUES(?,?) ON CONFLICT(mint) DO UPDATE SET until=excluded.until",
                       (s["mint"], time.time() + 86400))

    def card(self) -> dict:
        ns = self.night()
        trades = db.scalar("SELECT COUNT(*) FROM signals WHERE ts>=? AND status='bought'", (ns,), 0)
        last = db.one("SELECT s.*, c.name, c.symbol FROM signals s LEFT JOIN coins c ON c.mint=s.mint "
                      "WHERE s.status IN ('bought','sent') ORDER BY s.id DESC LIMIT 1")
        return {"name": "executor", "title": "Executor", "trades": trades, "executing": self.executing,
                "last": last, "stack_pct": config.get("executor.stack_pct"), "size_mode": config.get("executor.size_mode"),
                "venue": config.get("desk.venue")}

    def decisions(self, limit: int = 20) -> list[dict]:
        rows = db.query("SELECT s.*, c.name, c.symbol FROM signals s LEFT JOIN coins c ON c.mint=s.mint "
                        "ORDER BY s.id DESC LIMIT ?", (limit,))
        return [{"ts": r["ts"], "mint": r["mint"], "name": r["name"], "symbol": r["symbol"], "verdict": r["status"],
                 "reason": r["reason"] or (f"${r['size_usd']:.2f} · score {r['score']}" if r["size_usd"] else "")}
                for r in rows]
