"""Settings with defaults (STEP 10), validation, aliases and a change log.

Every number an agent uses comes from here. Agents read, they never write.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import db


@dataclass
class Spec:
    default: Any
    kind: type | str            # float, int, bool, str, or "choice"
    lo: float | None = None
    hi: float | None = None
    label: str = ""
    choices: tuple = ()
    aliases: tuple = ()
    unit: str = ""              # "%", "$", "s", "min", "h", "bps", "SOL", "x"
    step: float | None = None


def f(default, lo, hi, label, unit="", aliases=(), step=None):
    return Spec(float(default), float, lo, hi, label, (), aliases, unit, step)


def i(default, lo, hi, label, unit="", aliases=(), step=1):
    return Spec(int(default), int, lo, hi, label, (), aliases, unit, step)


def b(default, label, aliases=()):
    return Spec(bool(default), bool, None, None, label, (), aliases)


def c(default, choices, label, aliases=()):
    return Spec(default, "choice", None, None, label, tuple(choices), aliases)


def s(default, label, aliases=(), pattern=None):
    sp = Spec(default, str, None, None, label, (), aliases)
    sp.choices = (pattern,) if pattern else ()
    return sp


SPEC: dict[str, Spec] = {
    # ---- Scanner
    "scanner.min_liquidity_usd": f(40000, 0, 5_000_000, "Min liquidity", "$", ("liquidity", "min liq", "liq"), 1000),
    "scanner.max_top10_pct": f(25, 1, 100, "Max top 10 holders", "%", ("top10", "top 10")),
    "scanner.max_h1_change_pct": f(80, 0, 10000, "Max 1h change", "%", ("h1", "max pump")),
    "scanner.poll_s": i(60, 15, 600, "DexScreener poll", "s"),
    "scanner.min_pool_age_min": i(10, 0, 240, "Min pool age", "min", ("pool age",)),
    "scanner.max_wait_min": i(60, 5, 1440, "Max wait in room", "min", ("wait",)),
    "scanner.runner_h24_pct": f(300, 10, 100000, "Runner (copycat) 24h", "%", ("runner",), 10),
    "scanner.dex_rpm": i(250, 10, 290, "DexScreener requests/min", ""),
    "scanner.ws_silent_s": i(120, 30, 600, "Websocket silent reconnect", "s"),
    # ---- Auditor
    "auditor.enabled": b(True, "Take apart before buying", ("audit", "auditor")),
    "auditor.max_dev_pct": f(5, 0, 100, "Max dev holding", "%", ("dev",)),
    "auditor.max_top10_pct": f(25, 1, 100, "Max top 10 holders", "%"),
    "auditor.min_lp_locked_pct": f(99, 0, 100, "Min LP locked/burned", "%", ("lp",)),
    "auditor.min_pool_age_min": i(10, 0, 240, "Min pool age", "min"),
    "auditor.max_bundled": i(5, 1, 1000, "Max bundled wallets", "", ("bundled",)),
    "auditor.max_delegated_accounts": i(10, 1, 100000, "Max accounts per delegate", "", ("delegations",)),
    "auditor.retries": i(3, 0, 10, "Source retries", ""),
    "auditor.retry_gap_s": f(2, 0.5, 30, "Retry gap", "s"),
    "auditor.max_liq_drop_pct": f(50, 1, 100, "Liquidity pulled if down", "%"),
    # ---- Analyst
    "analyst.mode": c("rules", ("jev", "rules"), "Mode", ("analyst mode",)),
    "analyst.min_alive_6h": f(0.70, 0, 1, "Min alive 6h", "", ("alive",), 0.01),
    "analyst.min_organic": f(0.65, 0, 1, "Min organic", "", ("organic",), 0.01),
    "analyst.max_chase": f(0.30, 0, 1, "Max chase", "", ("chase",), 0.01),
    "analyst.min_score": i(52, 0, 100, "Min score", "", ("score", "min score")),
    "analyst.timeout_s": f(10, 2, 60, "Jev timeout", "s"),
    "analyst.rules_alive_volume_h1": f(25000, 100, 10_000_000, "Alive: 1h volume for 1.0", "$", (), 500),
    "analyst.rules_organic_buys_h1": i(150, 1, 100000, "Organic: 1h buys for 1.0", ""),
    "analyst.rules_bundled_penalty": f(0.5, 0, 1, "Organic x if bundled", "x", (), 0.05),
    "analyst.rules_bundled_from": i(3, 1, 1000, "Bundled penalty from", "wallets"),
    "analyst.rules_flush_drop_m5_pct": f(5, 0.1, 100, "Flush: 5m drop", "%"),
    "analyst.rules_chase_h1_pct": f(80, 1, 10000, "Chase: 1h change for 1.0", "%"),
    # ---- Executor
    "executor.size_mode": c("tiers", ("tiers", "pct"), "Sizing", ("sizing",)),
    "executor.tier_usd_52": f(0.50, 0, 1000, "Score 52-59", "$", ("tier 52",), 0.05),
    "executor.tier_usd_60": f(1.00, 0, 1000, "Score 60-69", "$", ("tier 60",), 0.05),
    "executor.tier_usd_70": f(2.00, 0, 1000, "Score 70-79", "$", ("tier 70",), 0.05),
    "executor.tier_usd_80": f(3.00, 0, 1000, "Score 80-89", "$", ("tier 80",), 0.05),
    "executor.tier_usd_90": f(4.00, 0, 1000, "Score 90+", "$", ("tier 90",), 0.05),
    "executor.max_positions": i(3, 1, 20, "Max open positions", "", ("positions", "max positions")),
    "executor.usdc_reserve_usd": f(1.00, 0, 100000, "Keep in USDC", "$", ("reserve",), 0.5),
    "executor.stack_pct": f(8, 0.1, 100, "Stack per trade (pct mode)", "%", ("stack pct",)),
    "executor.max_pool_pct": f(2, 0.01, 100, "Max % of pool liquidity", "%", ("pool pct",)),
    "executor.min_ticket_usd": f(47.50, 0, 100000, "Min ticket (pct mode)", "$", ("min ticket",), 0.5),
    "executor.below_min": c("bump", ("bump", "skip"), "Below min ticket", ("below min",)),
    "executor.recheck_liq_drop_pct": f(20, 1, 100, "Skip if liquidity down", "%"),
    "executor.recheck_price_up_pct": f(15, 1, 1000, "Skip if price ran", "%"),
    "executor.confirm_window_min": i(5, 1, 60, "FOMO tap window", "min"),
    # ---- Risk
    "risk.poll_s": i(15, 5, 120, "Price poll", "s"),
    "risk.stop_pct": f(6, 0.5, 90, "Stop loss", "%", ("stop", "stop loss"), 0.5),
    "risk.trail_start_pct": f(20, 1, 10000, "Trail starts at", "%", ("trail start",)),
    "risk.trail_pct": f(12, 0.5, 90, "Trail distance", "%", ("trail",), 0.5),
    "risk.timeout_h": f(3, 0.1, 72, "Timeout", "h", ("timeout",), 0.1),
    "risk.timeout_min_gain_pct": f(5, -100, 1000, "Timeout unless up", "%"),
    "risk.confirm_window_min": i(2, 1, 60, "Sold tap window", "min"),
    "risk.no_price_polls": i(4, 1, 100, "Warn after polls without price", ""),
    "risk.daily_loss_limit_usd": f(0, 0, 1_000_000, "Stop buying after daily loss (0 = off)", "$", ("loss limit",), 0.5),
    # ---- Wallet (own-wallet trading through Jupiter)
    "wallet.slippage_bps": i(300, 10, 5000, "Buy slippage", "bps", ("slippage",), 10),
    "wallet.sell_slippage_bps": i(800, 10, 5000, "Sell slippage", "bps", ("sell slippage",), 10),
    "wallet.max_priority_lamports": i(100000, 0, 10_000_000, "Max priority fee", "lamports", ("priority",), 10000),
    "wallet.min_sol_reserve": f(0.01, 0, 100, "Keep SOL for fees", "SOL", ("sol reserve",), 0.005),
    "wallet.max_price_impact_pct": f(3, 0.1, 100, "Max price impact", "%", ("impact",), 0.1),
    "wallet.max_trade_usd": f(5, 0.1, 100000, "Hard cap per trade", "$", ("max trade",), 0.5),
    "wallet.paper_fee_usd": f(0.01, 0, 100, "PAPER fee per swap", "$", (), 0.005),
    "wallet.reclaim_rent": b(True, "Close empty token accounts (get rent back)"),
    # ---- Desk
    "desk.mode": c("PAPER", ("PAPER", "LIVE"), "Mode"),
    "desk.venue": c("wallet", ("wallet", "fomo"), "Trades go through", ("venue",)),
    "desk.paper_start_stack": f(200, 1, 10_000_000, "PAPER start stack", "$", ("paper stack",), 10),
    "desk.stack": f(0, 0, 1e9, "LIVE stack (FOMO venue)", "$", ("stack",), 10),
    "desk.report_time": s("08:00", "Morning report", ("report time",), r"^([01]\d|2[0-3]):[0-5]\d$"),
    "desk.timezone": s("UTC", "Time zone", ("tz", "timezone")),
    "desk.notifier": c("webpush", ("webpush", "none"), "Notifications"),
    "desk.rpc_url": s("https://api.mainnet-beta.solana.com", "Solana RPC URL", ("rpc",), r"^https://\S+$"),
    "desk.push_rejects": b(False, "Push rejects too"),
    "desk.fees_ref_discount": f(0.10, 0, 1, "FOMO ref discount", "x", (), 0.01),
    "desk.fomo_handle": s("", "FOMO username", ("handle",), r"^@?[A-Za-z0-9_.]{0,40}$"),
}

GROUPS = ["scanner", "auditor", "analyst", "executor", "risk", "wallet", "desk"]
# Keys the app may not change through POST /api/settings (they have their own flow).
GUARDED = {"desk.mode"}

_values: dict[str, Any] = {}
_listeners: list[Callable[[str, Any, Any], None]] = []


class SettingError(ValueError):
    pass


def load() -> None:
    _values.clear()
    for k, sp in SPEC.items():
        _values[k] = sp.default
    for row in db.query("SELECT key, value FROM settings"):
        if row["key"] in SPEC:
            try:
                _values[row["key"]] = _coerce(row["key"], row["value"])
            except SettingError:
                pass


def get(key: str) -> Any:
    if not _values:
        for k, sp in SPEC.items():
            _values[k] = sp.default
    return _values[key]


def all_values() -> dict[str, Any]:
    return {k: get(k) for k in SPEC}


def on_change(fn: Callable[[str, Any, Any], None]) -> None:
    _listeners.append(fn)


def _coerce(key: str, raw: Any) -> Any:
    sp = SPEC[key]
    if sp.kind is bool:
        if isinstance(raw, bool):
            return raw
        t = str(raw).strip().lower()
        if t in ("1", "true", "on", "yes"):
            return True
        if t in ("0", "false", "off", "no"):
            return False
        raise SettingError(f"{key} takes on or off")
    if sp.kind == "choice":
        t = str(raw).strip()
        for ch in sp.choices:
            if t.lower() == ch.lower():
                return ch
        raise SettingError(f"{key} takes one of: {', '.join(sp.choices)}")
    if sp.kind is str:
        t = str(raw).strip()
        if sp.choices and sp.choices[0] and not re.match(sp.choices[0], t):
            raise SettingError(f"{key}: '{t}' is not a valid value")
        return t
    try:
        num = float(str(raw).replace("$", "").replace("%", "").replace(",", "").strip())
    except ValueError:
        raise SettingError(f"{key} takes a number between {fmt_range(key)}")
    if num != num:  # NaN
        raise SettingError(f"{key} takes a number between {fmt_range(key)}")
    if sp.lo is not None and num < sp.lo or sp.hi is not None and num > sp.hi:
        raise SettingError(f"{key} must be between {fmt_range(key)}")
    if sp.kind is int:
        if abs(num - round(num)) > 1e-9:
            raise SettingError(f"{key} takes a whole number between {fmt_range(key)}")
        return int(round(num))
    return float(num)


def fmt_range(key: str) -> str:
    sp = SPEC[key]
    def n(v):
        return f"{v:g}"
    return f"{n(sp.lo)} and {n(sp.hi)}"


def set_value(key: str, raw: Any, who: str = "app") -> tuple[Any, Any]:
    if key not in SPEC:
        raise SettingError(f"unknown setting {key}")
    new = _coerce(key, raw)
    old = get(key)
    if new == old:
        return old, new
    _values[key] = new
    now = time.time()
    db.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, str(new), now),
    )
    db.execute("INSERT INTO change_log(ts, key, old, new, who) VALUES(?,?,?,?,?)",
               (now, key, str(old), str(new), who))
    for fn in list(_listeners):
        try:
            fn(key, old, new)
        except Exception:  # a listener must never block a setting
            pass
    return old, new


def resolve(name: str) -> str | None:
    """Setting by full key, alias, or unique key suffix ("stop" -> risk.stop_pct)."""
    t = name.strip().lower().replace("-", " ").replace("_", " ")
    t = re.sub(r"\s+", " ", t)
    for k in SPEC:
        if t == k.lower() or t == k.lower().replace("_", " "):
            return k
    for k, sp in SPEC.items():
        if t in sp.aliases:
            return k
    hits = [k for k in SPEC if k.split(".", 1)[1].replace("_", " ") == t]
    if len(hits) == 1:
        return hits[0]
    hits = [k for k in SPEC if t in k.split(".", 1)[1].replace("_", " ")]
    if len(hits) == 1:
        return hits[0]
    return None


def display(key: str, value: Any = None) -> str:
    v = get(key) if value is None else value
    sp = SPEC[key]
    if key in ("risk.stop_pct",):
        return f"-{v:g}%"
    if sp.kind is bool:
        return "on" if v else "off"
    if sp.unit == "$":
        return f"${v:,.2f}" if isinstance(v, float) and v < 1000 else f"${v:,.0f}"
    if sp.unit == "%":
        return f"{v:g}%"
    if sp.unit:
        return f"{v:g} {sp.unit}" if isinstance(v, (int, float)) else f"{v}"
    return f"{v:g}" if isinstance(v, float) else str(v)


def describe() -> list[dict]:
    out = []
    for k, sp in SPEC.items():
        out.append({
            "key": k, "group": k.split(".")[0], "label": sp.label, "value": get(k),
            "default": sp.default, "type": sp.kind if isinstance(sp.kind, str) else sp.kind.__name__,
            "min": sp.lo, "max": sp.hi, "choices": list(sp.choices) if sp.kind == "choice" else [],
            "unit": sp.unit, "step": sp.step, "guarded": k in GUARDED,
        })
    return out


# ---- STEP 10 ticket check (only matters in "pct" sizing, the FOMO-style ticket floor)
def min_stack() -> float:
    return get("executor.min_ticket_usd") / (get("executor.stack_pct") / 100)


def ticket_check(stack: float) -> dict | None:
    if get("executor.size_mode") != "pct":
        return None
    ms = min_stack()
    if stack >= ms:
        return None
    if get("executor.below_min") == "bump":
        p = get("executor.min_ticket_usd") / stack * 100 if stack > 0 else 100
        return {"level": "info",
                "text": f"tickets bumped to ${get('executor.min_ticket_usd'):.2f} = {p:.1f}% of stack"}
    return {"level": "warn",
            "text": f"every coin will be skipped, stack under ${ms:,.2f}. Switch below_min to bump or raise the stack."}
