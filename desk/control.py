"""Actions shared by the API and the in-app commands: mode, pause, coin lookup."""
from __future__ import annotations

import time

from . import book, bus, config, db, registry, wallet


class ControlError(ValueError):
    pass


def paper_nights() -> int:
    return db.scalar("SELECT COUNT(*) FROM reports WHERE mode='PAPER'", (), 0)


async def set_mode(target: str, confirm: str | None, who: str = "app") -> dict:
    target = (target or "").upper()
    if target not in ("PAPER", "LIVE"):
        raise ControlError("mode is PAPER or LIVE")
    cur = config.get("desk.mode")
    if target == cur:
        return {"mode": cur, "changed": False}
    if cur == "LIVE" and db.scalar("SELECT COUNT(*) FROM positions WHERE mode='LIVE' AND status IN ('open','exiting')",
                                   (), 0):
        raise ControlError("LIVE positions are still open. Sell them first (Risk keeps guarding them).")
    if target == "LIVE":
        if confirm != "LIVE":
            raise ControlError("type LIVE to confirm")
        if paper_nights() < 1:
            raise ControlError("LIVE needs at least 1 full PAPER night first (the morning report closes a night)")
        if config.get("desk.venue") == "wallet":
            if not wallet.pubkey():
                raise ControlError("import a wallet first (Account tab)")
    config.set_value("desk.mode", target, who)
    if target == "LIVE" and config.get("desk.venue") == "wallet":
        await book.refresh_wallet()
    if book.run_start() == 0:
        book.start_run()
    bus.publish("mode", {"mode": target})
    return {"mode": target, "changed": True}


def set_paused(name: str, paused: bool) -> dict:
    name = name.lower()
    if name not in registry.ORDER:
        raise ControlError(f"no agent called {name}")
    if paused and name == "risk" and not registry.get("risk").can_pause():
        raise ControlError("Risk can't be paused while a position is open")
    db.state_set(f"paused.{name}", "1" if paused else "0")
    db.event("pause" if paused else "resume", name)
    bus.publish("agent", {"name": name, "paused": paused})
    return {"agent": name, "paused": paused}


def find_coins(x: str) -> list[dict]:
    x = (x or "").strip().lstrip("$")
    if not x:
        return []
    if len(x) >= 32:
        r = db.query("SELECT * FROM coins WHERE mint=?", (x,))
        return r or [{"mint": x, "name": None, "symbol": None, "unknown": True}]
    exact = db.query("SELECT * FROM coins WHERE lower(symbol)=lower(?) OR lower(name)=lower(?) "
                     "ORDER BY first_seen DESC LIMIT 10", (x, x))
    if exact:
        return exact
    return db.query("SELECT * FROM coins WHERE lower(symbol) LIKE lower(?) OR lower(name) LIKE lower(?) "
                    "ORDER BY first_seen DESC LIMIT 10", (f"%{x}%", f"%{x}%"))


def tonight() -> float:
    return book.night_start()


def heartbeats() -> dict:
    now = time.time()
    return {r["agent"]: now - r["ts"] for r in db.query("SELECT agent, ts FROM heartbeats")}
