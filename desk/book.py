"""Money bookkeeping: equity, paper cash, wallet balances, top-ups, the run and the night."""
from __future__ import annotations

import asyncio
import logging
import time

from . import bus, config, db, wallet

log = logging.getLogger("book")

_wallet_cache: dict = {}
_pending_flow = {"usdc": 0, "lamports": 0}


def mode() -> str:
    return config.get("desk.mode")


def live_wallet() -> bool:
    return mode() == "LIVE" and config.get("desk.venue") == "wallet"


# ---------------------------------------------------------------- run / night
def run_start() -> float:
    return float(db.state_get(f"run_start_ts.{mode()}", "0") or 0)


def run_start_equity() -> float:
    v = db.state_get(f"run_start_equity.{mode()}")
    if v is None:
        return config.get("desk.paper_start_stack") if mode() == "PAPER" else 0.0
    return float(v)


def start_run(equity_value: float | None = None) -> None:
    m = mode()
    if m == "PAPER":
        start = config.get("desk.paper_start_stack")
        db.state_set("paper_cash", start)
        equity_value = start
    elif equity_value is None:
        equity_value = equity()
    db.state_set(f"run_start_ts.{m}", time.time())
    db.state_set(f"run_start_equity.{m}", round(equity_value, 6))
    db.event("run_start", f"{m} {equity_value:.2f}")


def night_start() -> float:
    v = db.state_get("night_start")
    if v is None:
        db.state_set("night_start", time.time())
        return time.time()
    return float(v)


def night_start_equity() -> float:
    ns = night_start()
    r = db.one("SELECT value FROM balance WHERE ts>=? AND mode=? ORDER BY ts ASC LIMIT 1", (ns, mode()))
    return r["value"] if r else run_start_equity()


# ---------------------------------------------------------------- paper cash
def paper_cash() -> float:
    v = db.state_get("paper_cash")
    if v is None:
        db.state_set("paper_cash", config.get("desk.paper_start_stack"))
        return config.get("desk.paper_start_stack")
    return float(v)


def paper_cash_add(delta: float) -> float:
    v = round(paper_cash() + delta, 6)
    db.state_set("paper_cash", v)
    return v


# ---------------------------------------------------------------- wallet
def note_flow(usdc_raw: int, lamports: int) -> None:
    """Called by trader inside wallet.trade_lock: flows our own swaps caused (not top-ups)."""
    _pending_flow["usdc"] += usdc_raw
    _pending_flow["lamports"] += lamports


def wallet_cached() -> dict:
    return dict(_wallet_cache)


async def refresh_wallet() -> dict:
    """Read SOL + USDC. Any change our swaps don't explain is booked as a top-up (or withdrawal)."""
    if not wallet.pubkey():
        _wallet_cache.clear()
        return {}
    async with wallet.trade_lock:
        b = await wallet.balances()
        prev = dict(_wallet_cache)
        flow = dict(_pending_flow)
        _pending_flow["usdc"] = 0
        _pending_flow["lamports"] = 0
    if prev.get("wallet") == b.get("wallet") and "usdc" in prev:
        du = round(b["usdc"] * 1e6) - round(prev["usdc"] * 1e6) - flow["usdc"]
        dl = b["lamports"] - prev["lamports"] - flow["lamports"]
        usd = du / 1e6 + dl / 1e9 * (b.get("sol_price") or 0)
        if abs(du) >= 10_000 or abs(dl) >= 1_000_000:  # >= $0.01 USDC or >= 0.001 SOL
            db.execute("INSERT INTO ledger(ts, kind, usd, note, mode) VALUES(?,?,?,?,?)",
                       (time.time(), "topup", round(usd, 4), f"usdc {du/1e6:+.4f} sol {dl/1e9:+.6f}", "LIVE"))
            log.info("top-up %+.4f USD", usd)
    _wallet_cache.clear()
    _wallet_cache.update(b)
    return b


# ---------------------------------------------------------------- positions & equity
def open_positions(all_modes: bool = False) -> list[dict]:
    if all_modes:  # Risk guards every open position, PAPER and LIVE, whatever the current mode
        return db.query("SELECT * FROM positions WHERE status IN ('open','exiting') ORDER BY opened_at")
    return db.query("SELECT * FROM positions WHERE status IN ('open','exiting') AND mode=? ORDER BY opened_at",
                    (mode(),))


def position_value(p: dict) -> float:
    px = p.get("last_price") or p["entry_price"]
    if p.get("tokens_raw") and p.get("decimals") is not None and p["venue"] == "wallet" and p["mode"] == "LIVE":
        return int(p["tokens_raw"]) / 10 ** int(p["decimals"]) * px
    return p["size_usd"] * px / p["entry_price"] if p["entry_price"] else 0.0


def invested() -> float:
    return sum(p["size_usd"] for p in open_positions())


def equity() -> float:
    opens = sum(position_value(p) for p in open_positions())
    m = mode()
    if m == "PAPER":
        return round(paper_cash() + opens, 6)
    if config.get("desk.venue") == "wallet":
        w = _wallet_cache
        if not w:
            return round(opens, 6)
        return round(w.get("usdc", 0) + w.get("sol_usd", 0) + opens, 6)
    closed = db.scalar("SELECT SUM(pnl_usd) FROM positions WHERE mode='LIVE' AND closed_at>=?", (run_start(),), 0)
    return round(config.get("desk.stack") + closed + sum(position_value(p) - p["size_usd"] for p in open_positions()), 6)


def free_cash() -> float:
    m = mode()
    if m == "PAPER":
        return paper_cash()
    if config.get("desk.venue") == "wallet":
        return (_wallet_cache.get("usdc") or 0) - config.get("executor.usdc_reserve_usd")
    return config.get("desk.stack") - invested()


def stack_value() -> float:
    """What 'stack' means for % of stack."""
    return equity() if mode() == "PAPER" or config.get("desk.venue") == "wallet" else config.get("desk.stack")


def breakdown() -> dict:
    rs = run_start()
    m = mode()
    realized = db.scalar("SELECT SUM(pnl_usd) FROM positions WHERE mode=? AND status='closed' AND closed_at>=?",
                         (m, rs), 0)
    fees = db.scalar("SELECT SUM(fees_usd) FROM positions WHERE mode=? AND opened_at>=?", (m, rs), 0)
    unreal = sum(position_value(p) - p["size_usd"] for p in open_positions())
    topups = db.scalar("SELECT SUM(usd) FROM ledger WHERE kind='topup' AND mode=? AND ts>=?", (m, rs), 0)
    eq = equity()
    start = run_start_equity()
    base = start + topups
    pnl = eq - base
    return {"equity": round(eq, 4), "start": round(start, 4), "realized": round(realized, 4),
            "unrealized": round(unreal, 4), "fees": round(fees, 4), "topups": round(topups, 4),
            "pnl": round(pnl, 4), "pnl_pct": round(pnl / base * 100, 2) if base else 0.0,
            "multiplier": round(eq / base, 4) if base else 1.0}


def today_loss() -> float:
    day0 = time.time() - 86400
    v = db.scalar("SELECT SUM(pnl_usd) FROM positions WHERE mode=? AND status='closed' AND closed_at>=?",
                  (mode(), day0), 0)
    return -v if v < 0 else 0.0


async def sample_loop() -> None:
    """Every minute: wallet read (LIVE), equity row in db.balance, SSE 'balance'."""
    while True:
        try:
            if live_wallet() or wallet.pubkey():
                await refresh_wallet()
            eq = equity()
            db.execute("INSERT INTO balance(ts, value, mode) VALUES(?,?,?)", (time.time(), eq, mode()))
            bus.publish("balance", breakdown())
            db.heartbeat("book")
        except Exception as e:
            log.warning("sample: %s", e)
        await asyncio.sleep(60)
