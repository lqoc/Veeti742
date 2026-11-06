"""AGENT 5 · RISK. Owns every open position from fill to exit. The rules don't bend."""
from __future__ import annotations

import asyncio
import time

from .. import book, bus, config, db, fees, links, notifier, sources, trader
from .base import Agent, iso, short
from .scanner import record_prices


def check(p: dict, price: float, now: float | None = None) -> tuple[str | None, float, bool]:
    """Pure exit rules, in order. Returns (reason or None, new peak, trail_active)."""
    now = now or time.time()
    entry = p["entry_price"]
    peak = max(p.get("peak_price") or entry, price)
    trail = bool(p.get("trail_active"))
    if price <= p["stop_price"]:
        return "stop", peak, trail
    if price >= entry * (1 + config.get("risk.trail_start_pct") / 100):
        trail = True
    if trail and price <= peak * (1 - config.get("risk.trail_pct") / 100):
        return "trail", peak, trail
    if now - p["opened_at"] >= config.get("risk.timeout_h") * 3600 and \
            price < entry * (1 + config.get("risk.timeout_min_gain_pct") / 100):
        return "timeout", peak, trail
    return None, peak, trail


def exit_lines(p: dict) -> dict:
    trail_line = (p.get("peak_price") or p["entry_price"]) * (1 - config.get("risk.trail_pct") / 100)
    current = max(p["stop_price"], trail_line) if p.get("trail_active") else p["stop_price"]
    return {"stop": p["stop_price"], "trail": trail_line if p.get("trail_active") else None,
            "trail_starts": p["entry_price"] * (1 + config.get("risk.trail_start_pct") / 100),
            "current": current,
            "timeout_at": p["opened_at"] + config.get("risk.timeout_h") * 3600}


class Risk(Agent):
    name = "risk"

    def __init__(self) -> None:
        super().__init__()
        self.exiting: set[int] = set()
        self.sell_failures: dict[int, int] = {}
        self.last_exit: dict | None = None
        self.missed_polls = 0
        config.on_change(self.on_setting)

    def on_setting(self, key, old, new) -> None:
        """A new stop applies to open positions only if it is tighter. Never moves a stop down."""
        if key != "risk.stop_pct":
            return
        for p in book.open_positions(all_modes=True):
            line = p["entry_price"] * (1 - new / 100)
            if line > p["stop_price"]:
                db.execute("UPDATE positions SET stop_price=?, stop_pct=? WHERE id=?", (line, new, p["id"]))
                self.log.info("%s stop tightened to -%s%%", p["mint"], new)

    def can_pause(self) -> bool:
        return not book.open_positions(all_modes=True)

    async def run(self) -> None:
        while True:
            self.beat()
            try:
                await self.poll_once()
            except sources.RateLimited:
                self.missed_polls += 1
                self.log.info("dexscreener 429, backing off 30s (missed polls %s)", self.missed_polls)
                await asyncio.sleep(30)
                continue
            except Exception as e:
                self.log.info("poll error %s: %s", e.__class__.__name__, e)
            await asyncio.sleep(config.get("risk.poll_s"))

    async def poll_once(self) -> None:
        opens = book.open_positions(all_modes=True)
        if not opens:
            return
        pairs = await sources.dex_tokens([p["mint"] for p in opens])
        record_prices(pairs)
        now = time.time()
        for p in opens:
            if p["id"] in self.exiting:
                continue
            v = sources.pair_view(pairs.get(p["mint"]))
            price = v.get("price")
            if price is None:
                n = (p.get("no_price_polls") or 0) + 1
                db.execute("UPDATE positions SET no_price_polls=? WHERE id=?", (n, p["id"]))
                self.log.info("%s no price (%s polls)", p["mint"], n)
                if n == config.get("risk.no_price_polls"):
                    await notifier.push(f"{p['name'] or short(p['mint'])}: no price for 1 min", mint=p["mint"])
                continue
            if p["status"] == "exiting":  # LIVE: retrying a failed sell, or waiting for [Sold]
                db.execute("UPDATE positions SET last_price=? WHERE id=?", (price, p["id"]))
                if p["venue"] == "wallet":
                    self.start_exit(p, p["exit_reason"] or "stop", price)
                continue
            if v.get("liq") is not None and v["liq"] <= 0:
                self.start_exit(p, "pulled", p.get("last_price") or price)
                continue
            reason, peak, trail = check(p, price, now)
            db.execute("UPDATE positions SET last_price=?, peak_price=?, trail_active=?, no_price_polls=0 WHERE id=?",
                       (price, peak, int(trail), p["id"]))
            p.update(last_price=price, peak_price=peak, trail_active=int(trail))
            lines = exit_lines(p)
            self.log.info("%s price=%.10g peak=%.10g stop=%.10g trail=%s action=%s", p["mint"], price, peak,
                          p["stop_price"], f"{lines['trail']:.10g}" if lines["trail"] else "off", reason or "hold")
            bus.publish("price", {"mint": p["mint"], "price": price, "position": p["id"], "lines": lines})
            if reason:
                self.start_exit(p, reason, price)

    def start_exit(self, p: dict, reason: str, price: float) -> None:
        if p["id"] in self.exiting:
            return
        self.exiting.add(p["id"])
        asyncio.create_task(self._exit(p, reason, price))

    async def manual_exit(self, pid: int) -> dict:
        p = db.one("SELECT * FROM positions WHERE id=? AND status IN ('open','exiting')", (pid,))
        if not p:
            return {"ok": False, "reason": "no open position"}
        price = p.get("last_price") or p["entry_price"]
        try:
            v = sources.pair_view((await sources.dex_tokens([p["mint"]])).get(p["mint"]))
            price = v.get("price") or price
        except sources.SourceError:
            pass
        self.start_exit(p, "manual", price)
        return {"ok": True}

    async def _exit(self, p: dict, reason: str, price: float) -> None:
        try:
            await self._exit_inner(p, reason, price)
        except Exception as e:
            self.log.info("%s exit error %s", p["mint"], e)
        finally:
            self.exiting.discard(p["id"])

    async def _exit_inner(self, p: dict, reason: str, price: float) -> None:
        name = p["name"] or p["symbol"] or short(p["mint"])
        link = links.coin_url(p["mint"], "solana")
        if p["mode"] == "PAPER":
            value = p["size_usd"] * price / p["entry_price"]
            fee_out = fees.side(value)
            total_fees = (p["fees_usd"] or 0) + fee_out
            pnl = value - p["size_usd"] - total_fees
            book.paper_cash_add(value - fee_out)
            self.close(p, reason, price, pnl, total_fees)
        elif p["venue"] == "wallet":
            db.execute("UPDATE positions SET status='exiting', exit_reason=?, exit_started=COALESCE(exit_started,?) "
                       "WHERE id=?", (reason, time.time(), p["id"]))
            t = await trader.sell(p["mint"])
            if not t.ok and t.reason == "nothing to sell":
                # An RPC can lag right after a buy. Only after 3 reads of an empty balance do we accept
                # that the tokens left the wallet some other way (you sold them yourself).
                n = self.sell_failures.get(p["id"], 0) + 1
                self.sell_failures[p["id"]] = n
                if n < 3:
                    self.log.info("%s wallet shows no tokens yet (%s/3)", p["mint"], n)
                    return
                t = trader.Trade(True, sig=None, usd=0.0, price=price)
                reason = "manual"
            if not t.ok:
                n = self.sell_failures.get(p["id"], 0) + 1
                self.sell_failures[p["id"]] = n
                self.log.info("%s sell failed (%s): %s", p["mint"], n, t.reason)
                if n == 1 or n % 3 == 0:
                    await notifier.push(f"Sell failed {name} · {t.reason} · retrying, or sell it yourself", mint=p["mint"])
                return
            total_fees = (p["fees_usd"] or 0) + t.sol_cost_usd
            refund = await trader.close_empty(p["mint"])
            if refund.ok:
                total_fees += refund.sol_cost_usd
            pnl = t.usd - p["size_usd"] - total_fees
            db.execute("UPDATE positions SET sell_sig=? WHERE id=?", (t.sig, p["id"]))
            self.close(p, reason, t.price or price, pnl, total_fees)
            await book.refresh_wallet()
        else:  # LIVE via FOMO: you sell inside FOMO, then tap [Sold]
            db.execute("UPDATE positions SET status='exiting', exit_reason=?, exit_price=?, exit_started=? WHERE id=?",
                       (reason, price, time.time(), p["id"]))
            await notifier.push(self.line(p, reason, price, None), mint=p["mint"])
            await asyncio.sleep(config.get("risk.confirm_window_min") * 60)
            still = db.one("SELECT status FROM positions WHERE id=?", (p["id"],))
            if still and still["status"] == "exiting":
                await notifier.push(f"{name} still open · sell on FOMO", mint=p["mint"])
            return
        q = db.one("SELECT * FROM positions WHERE id=?", (p["id"],))
        await notifier.push(self.line(q, reason, q["exit_price"], q["pnl_usd"]), mint=p["mint"])
        out = {"agent": "risk", "mint": p["mint"], "name": name, "ts": iso(), "reason": reason,
               "entryPrice": p["entry_price"], "exitPrice": q["exit_price"], "peakPrice": q["peak_price"],
               "pnlUsd": round(q["pnl_usd"], 4), "pct": round(q["pnl_usd"] / p["size_usd"] * 100, 2),
               "feesUsd": round(q["fees_usd"], 4), "stopPct": p["stop_pct"], "mode": p["mode"], "status": "closed",
               "link": link}
        self.last_exit = out
        bus.publish("exit", out)

    def tap_sold(self, pid: int, price: float | None = None) -> bool:
        p = db.one("SELECT * FROM positions WHERE id=? AND status='exiting'", (pid,))
        if not p:
            return False
        px = price or p["exit_price"] or p["last_price"]
        value = p["size_usd"] * px / p["entry_price"]
        pnl = value - p["size_usd"] - (p["fees_usd"] or 0)
        self.close(p, p["exit_reason"] or "manual", px, pnl, p["fees_usd"] or 0)
        return True

    def line(self, p: dict, reason: str, price: float, pnl: float | None) -> str:
        name = p["name"] or p["symbol"] or short(p["mint"])
        if pnl is None:
            pnl = p["size_usd"] * price / p["entry_price"] - p["size_usd"]
        pct = pnl / p["size_usd"] * 100 if p["size_usd"] else 0
        sign = "+" if pnl >= 0 else "-"
        if reason == "stop":
            return f"{name} hit the -{p['stop_pct']:g}% stop · -${abs(pnl):.2f}"
        if reason == "timeout":
            return f"Sell {name}, dead money · {pct:+.1f}% in {config.get('risk.timeout_h'):g}h"
        if reason == "pulled":
            return f"{name} liquidity pulled · {sign}${abs(pnl):.2f}"
        return f"Sold {name} · {sign}${abs(pnl):.2f} ({pct:+.0f}%)"

    def close(self, p: dict, reason: str, price: float, pnl: float, total_fees: float) -> None:
        now = time.time()
        db.execute("UPDATE positions SET status='closed', closed_at=?, exit_price=?, exit_reason=?, pnl_usd=?, "
                   "fees_usd=?, last_price=? WHERE id=?", (now, price, reason, pnl, total_fees, price, p["id"]))
        name = p["name"] or p["symbol"] or short(p["mint"])
        pct = pnl / p["size_usd"] * 100 if p["size_usd"] else 0
        sign = "+" if pnl >= 0 else "-"
        if reason in ("stop", "pulled"):
            sub = f"Risk · -{p['stop_pct']:g}% stop" if reason == "stop" else "Risk · liquidity pulled"
            db.execute("INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value, value2, ref_id) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (now, "stopped", p["mint"], "risk", f"Stopped {name}", sub,
                                                     f"{sign}${abs(pnl):.2f}", f"{pct:+.1f}%", p["id"]))
        else:
            sub = {"trail": "Risk · trailing stop", "timeout": "Risk · timeout", "manual": "Risk · you sold"}.get(
                reason, f"Risk · {reason}")
            db.execute("INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value, value2, ref_id) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (now, "sold", p["mint"], "risk", f"Sold {name}", sub,
                                                     f"{sign}${abs(pnl):.2f}", f"{pct:+.0f}%", p["id"]))
        self.last_exit = {"mint": p["mint"], "name": name, "reason": reason, "pnlUsd": pnl}
        self.log.info("%s exit %s at %.10g pnl=%.4f fees=%.4f", p["mint"], reason, price, pnl, total_fees)
        bus.publish("activity", {"mint": p["mint"]})

    def card(self) -> dict:
        opens = book.open_positions(all_modes=True)
        last = db.one("SELECT name, symbol, exit_reason, pnl_usd FROM positions WHERE status='closed' "
                      "ORDER BY closed_at DESC LIMIT 1")
        if last:
            last_text = "Trailed out" if last["exit_reason"] == "trail" else (
                "Stopped" if last["exit_reason"] in ("stop", "pulled") else "Sold")
        else:
            last_text = None
        return {"name": "risk", "title": "Risk", "status": "Guarding" if opens else "Idle",
                "open": [{"id": p["id"], "mint": p["mint"], "name": p["name"], "symbol": p["symbol"],
                          "pnl": book.position_value(p) - p["size_usd"]} for p in opens],
                "last": last, "last_text": last_text, "stop_pct": config.get("risk.stop_pct"),
                "trail_pct": config.get("risk.trail_pct"), "locked": bool(opens)}

    def decisions(self, limit: int = 20) -> list[dict]:
        rows = db.query("SELECT * FROM positions ORDER BY COALESCE(closed_at, opened_at) DESC LIMIT ?", (limit,))
        return [{"ts": r["closed_at"] or r["opened_at"], "mint": r["mint"], "name": r["name"], "symbol": r["symbol"],
                 "verdict": r["exit_reason"] or r["status"],
                 "reason": (f"{'+' if (r['pnl_usd'] or 0) >= 0 else '-'}${abs(r['pnl_usd'] or 0):.2f}"
                            if r["status"] == "closed" else "guarding")} for r in rows]
