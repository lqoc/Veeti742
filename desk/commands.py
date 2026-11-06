"""Talking to the desk (STEP 9) without Telegram: the same commands typed in the app.

Every coin mentioned in a reply carries its links.coin_url. Nothing here places an order by itself:
a wallet sell only happens when you tap the [Sell now] button it returns.
"""
from __future__ import annotations

import re
import time

from . import book, config, control, db, links, notifier, registry
from .agents.base import short

HELP = ("Commands: buy X · sell X · why X · pause Scanner · resume Scanner · set stop to -8% · "
        "stack 1240 · status · paper · live · invite · report")


def coin_btn(c: dict, label: str = "Open on FOMO") -> dict:
    return {"label": label, "url": links.coin_url(c["mint"], "solana")}


def label(c: dict) -> str:
    return c.get("symbol") or c.get("name") or short(c["mint"])


def reply(text: str, buttons: list | None = None) -> dict:
    return {"text": text, "buttons": buttons or []}


def pick(x: str):
    hits = control.find_coins(x)
    if not hits:
        return None, reply(f"I don't know {x}. Send me its mint address.")
    if len(hits) > 1:
        return None, reply(f"{x} matches {len(hits)} coins. Which one?",
                           [coin_btn(c, f"{label(c)} · {short(c['mint'])}") for c in hits])
    return hits[0], None


async def handle(text: str) -> dict:
    t = (text or "").strip()
    if not t:
        return reply(HELP)
    t = t.lstrip("/")
    low = t.lower()
    if t == "LIVE":
        try:
            r = await control.set_mode("LIVE", "LIVE", "command")
            return reply("Mode LIVE. Trades now use real money." if r["changed"] else "Already LIVE.")
        except control.ControlError as e:
            return reply(str(e))
    if low in ("paper", "live"):
        if low == "live":
            return reply("Type LIVE (capitals) to switch to real money.")
        try:
            await control.set_mode("PAPER", None, "command")
            return reply("Mode PAPER.")
        except control.ControlError as e:
            return reply(str(e))
    if low in ("help", "?"):
        return reply(HELP)
    if low == "invite":
        return reply(f"New to FOMO? {links.SIGNUP_URL}", [{"label": "Invite a friend", "url": links.SIGNUP_URL}])
    if low == "report":
        r = db.one("SELECT id FROM reports ORDER BY ts DESC LIMIT 1")
        if not r:
            return reply("No morning report yet. The first one comes at " + config.get("desk.report_time") + ".")
        return reply("Last morning report", [{"label": "Open report", "url": f"/report/{r['id']}"}])
    if low == "status":
        return status()
    m = re.match(r"^(pause|resume)\s+(\w+)$", low)
    if m:
        try:
            r = control.set_paused(m.group(2), m.group(1) == "pause")
            return reply(f"{r['agent'].title()} {'paused' if r['paused'] else 'running'}.")
        except control.ControlError as e:
            return reply(str(e))
    m = re.match(r"^stack\s+\$?([\d,.]+)$", low)
    if m:
        return set_setting("desk.stack", m.group(1))
    m = re.match(r"^set\s+(.+?)\s+(?:to|=)\s+(.+)$", low)
    if m:
        key = config.resolve(m.group(1))
        if not key:
            return reply(f"No setting called '{m.group(1)}'. Settings are in the Account tab.")
        val = m.group(2).strip()
        if key in ("risk.stop_pct", "risk.trail_pct"):
            val = val.replace("-", "")
        return set_setting(key, val)
    m = re.match(r"^(buy|sell|why)\s+(.+)$", t, re.I)
    if m:
        verb, x = m.group(1).lower(), m.group(2).strip()
        coin, err = pick(x)
        if err:
            return err
        return await {"buy": cmd_buy, "sell": cmd_sell, "why": cmd_why}[verb](coin)
    return reply(HELP)


def set_setting(key: str, val: str) -> dict:
    try:
        old, new = config.set_value(key, val, "command")
    except config.SettingError as e:
        return reply(str(e))
    name = key.split(".", 1)[1].replace("_pct", "").replace("_", " ")
    out = f"{name} {config.display(key, old)} -> {config.display(key, new)}"
    if key in ("desk.stack", "executor.stack_pct", "executor.below_min"):
        w = config.ticket_check(config.get("desk.stack"))
        if w:
            out += f"\n{w['text']}"
    return reply(out)


def status() -> dict:
    b = book.breakdown()
    lines = [f"Mode {config.get('desk.mode')} · venue {config.get('desk.venue')}",
             f"Balance ${b['equity']:.2f} ({b['pnl']:+.2f}, {b['multiplier']:.2f}x)"]
    btns = []
    opens = book.open_positions(all_modes=True)
    if not opens:
        lines.append("No open positions.")
    for p in opens:
        pnl = book.position_value(p) - p["size_usd"]
        lines.append(f"{p['symbol'] or p['name']} · ${p['size_usd']:.2f} in · {pnl:+.2f} · {p['mode']}")
        btns.append(coin_btn(p, f"Sell {p['symbol'] or p['name']} on FOMO"))
        if p["mode"] == "LIVE" and p["venue"] == "wallet":
            btns.append({"label": f"Sell {p['symbol'] or p['name']} now", "action": f"/api/positions/{p['id']}/sell"})
    paused = [n for n in registry.ORDER if db.state_get(f"paused.{n}", "0") == "1"]
    if paused:
        lines.append("Paused: " + ", ".join(paused))
    return reply("\n".join(lines), btns)


async def cmd_buy(c: dict) -> dict:
    s = db.one("SELECT * FROM signals WHERE mint=? AND ts>=? AND status IN ('sent','bought') ORDER BY id DESC LIMIT 1",
               (c["mint"], control.tonight()))
    if s:
        res = await notifier.push(f"Buy {label(c)} · ${s['size_usd']:.2f} · {s['stack_pct'] or 0:g}% of stack",
                                  mint=c["mint"])
        return reply(f"Resent: Buy {label(c)} · ${s['size_usd']:.2f}", [coin_btn(c, "Buy on FOMO")])
    audit = await registry.get("auditor").audit({"mint": c["mint"], "pool": c.get("pool"), "name": c.get("name"),
                                                 "symbol": c.get("symbol")}, ad_hoc=True)
    rows = [f"{x['n']} {x['name']}: {'ok' if x['pass'] else ('red' if x['pass'] is False else '-')} · {x['value']}"
            for x in audit["layers"]]
    head = "Cleared 8/8" if audit["passed"] else f"Rejected · {audit['reason']}"
    return reply(f"{label(c)} · {head}\n" + "\n".join(rows) + "\nnot sized, not on the desk",
                 [coin_btn(c, "Buy on FOMO")])


async def cmd_sell(c: dict) -> dict:
    p = db.one("SELECT * FROM positions WHERE mint=? AND status IN ('open','exiting') ORDER BY id DESC LIMIT 1",
               (c["mint"],))
    if not p:
        return reply(f"No open {label(c)} position.", [coin_btn(c)])
    btns = [coin_btn(c, "Sell on FOMO")]
    if p["mode"] == "LIVE" and p["venue"] == "wallet":
        btns.insert(0, {"label": "Sell now from wallet", "action": f"/api/positions/{p['id']}/sell"})
    elif p["mode"] == "PAPER":
        btns.insert(0, {"label": "Close PAPER position", "action": f"/api/positions/{p['id']}/sell"})
    return reply(f"{label(c)} · ${p['size_usd']:.2f} in · {p['mode']}", btns)


async def cmd_why(c: dict) -> dict:
    mint = c["mint"]
    ev = []
    for r in db.query("SELECT ts, verdict, reason FROM scans WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,)):
        ev.append((r["ts"], "Scanner", r["verdict"], r["reason"] or "passed → auditor"))
    red = db.one("SELECT ts, layer, name, value_text FROM audits WHERE mint=? AND pass=0 ORDER BY id DESC LIMIT 1",
                 (mint,))
    if red:
        ev.append((red["ts"], "Auditor", f"layer {red['layer']} {red['name']}", red["value_text"]))
    v = db.one("SELECT ts, pass, reason, score, model FROM verdicts WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))
    if v:
        who = "Analyst (rules)" if v["model"] == "rules" else "Analyst"
        ev.append((v["ts"], who, "pass" if v["pass"] else "reject", v["reason"] or f"score {v['score']}"))
    s = db.one("SELECT ts, status, reason FROM signals WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,))
    if s:
        ev.append((s["ts"], "Executor", s["status"], s["reason"] or ""))
    p = db.one("SELECT closed_at, exit_reason, pnl_usd FROM positions WHERE mint=? AND status='closed' "
               "ORDER BY id DESC LIMIT 1", (mint,))
    if p:
        ev.append((p["closed_at"], "Risk", p["exit_reason"], f"{p['pnl_usd']:+.2f}"))
    if not ev:
        return reply(f"{label(c)} never reached the desk tonight.", [coin_btn(c)])
    ev.sort(key=lambda e: e[0])
    lines = [f"{time.strftime('%H:%M', time.gmtime(ts))} UTC · {who} · {what} · {why}" for ts, who, what, why in ev]
    return reply(f"{label(c)}\n" + "\n".join(lines), [coin_btn(c)])
