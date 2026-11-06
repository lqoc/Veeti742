"""The only 2 routines (STEP 12): desk-health (hourly) and night-report (daily at report_time).
Plus the supervisor that keeps each agent task alive."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from . import book, config, db, notifier, registry, report

log = logging.getLogger("health")
tasks: dict[str, asyncio.Task] = {}
_down_since: dict[str, float] = {}


async def _guard(name: str) -> None:
    agent = registry.get(name)
    while True:
        try:
            await agent.run()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("agent %s crashed: %s", name, e)
            db.event("agent_crash", f"{name}: {e}")
            await asyncio.sleep(5)


def start_agent(name: str) -> None:
    t = tasks.get(name)
    if t and not t.done():
        t.cancel()
    tasks[name] = asyncio.create_task(_guard(name), name=f"agent:{name}")


async def desk_health_once() -> list[str]:
    msgs = []
    now = time.time()
    beats = {r["agent"]: r["ts"] for r in db.query("SELECT agent, ts FROM heartbeats")}
    for name in registry.ORDER:
        ts = beats.get(name, 0)
        if now - ts > 120:
            down = (now - ts) / 60 if ts else 0
            start_agent(name)
            msg = f"Restarted {name}, down {down:.0f} min"
            msgs.append(msg)
            db.event("restart", msg)
            await notifier.push(msg, trade=False)
    n = registry.get("scanner").reconnects_last_hour()
    if n > 10:
        msg = f"Scanner websocket reconnected {n} times in the last hour"
        msgs.append(msg)
        await notifier.push(msg, trade=False)
    return msgs


async def desk_health_loop() -> None:
    await asyncio.sleep(180)
    while True:
        try:
            await desk_health_once()
        except Exception as e:
            log.warning("desk-health: %s", e)
        await asyncio.sleep(3600)


def next_report_at(now: float | None = None) -> float:
    now = now or time.time()
    try:
        tz = ZoneInfo(config.get("desk.timezone"))
    except Exception:
        tz = ZoneInfo("UTC")
    hh, mm = (int(x) for x in config.get("desk.report_time").split(":"))
    local = dt.datetime.fromtimestamp(now, tz)
    target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target.timestamp() <= now:
        target += dt.timedelta(days=1)
    return target.timestamp()


async def night_report_once() -> str:
    ns = book.night_start()
    rid = report.save(ns)
    await notifier.push("Morning report is ready", url=f"/report/{rid}", trade=False)
    db.execute("DELETE FROM skips")
    db.state_set("night_start", time.time())
    registry.get("auditor").layer8_down_alerted = False
    w = config.ticket_check(book.stack_value())
    if w and w["level"] == "warn":
        await notifier.push(w["text"], trade=False)
    return rid


async def night_report_loop() -> None:
    while True:
        cfg = (config.get("desk.report_time"), config.get("desk.timezone"))
        target = next_report_at()
        while time.time() < target:
            await asyncio.sleep(min(60, max(1, target - time.time())))
            if (config.get("desk.report_time"), config.get("desk.timezone")) != cfg:
                break  # report time changed: recompute
        else:
            try:
                await night_report_once()
            except Exception as e:
                log.warning("night-report: %s", e)
            await asyncio.sleep(61)
