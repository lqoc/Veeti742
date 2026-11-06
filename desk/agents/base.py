"""Shared agent plumbing: per-agent daily-rotated log, heartbeat, pause flag, night helpers."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import logging.handlers
import os
import time
from pathlib import Path

from .. import book, db

LOGS = Path(os.environ.get("DESK_LOGS") or Path(__file__).resolve().parent.parent / "logs")


def agent_logger(name: str) -> logging.Logger:
    lg = logging.getLogger(f"agent.{name}")
    if not lg.handlers:
        LOGS.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.TimedRotatingFileHandler(LOGS / f"{name}.log", when="midnight", backupCount=14,
                                                      encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%SZ"))
        h.formatter.converter = time.gmtime  # type: ignore[union-attr]
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
    return lg


def iso(ts: float | None = None) -> str:
    return dt.datetime.fromtimestamp(ts or time.time(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short(addr: str | None) -> str:
    if not addr:
        return "?"
    return f"{addr[:4]}...{addr[-3:]}"


def money(v: float | None) -> str:
    if v is None:
        return "?"
    a = abs(v)
    if a >= 1e6:
        return f"${v/1e6:.1f}M"
    if a >= 1e3:
        return f"${v/1e3:.1f}K"
    return f"${v:,.2f}"


class Agent:
    name = "agent"

    def __init__(self) -> None:
        self.log = agent_logger(self.name)

    @property
    def paused(self) -> bool:
        return db.state_get(f"paused.{self.name}", "0") == "1"

    def beat(self) -> None:
        db.heartbeat(self.name)

    def night(self) -> float:
        return book.night_start()

    async def run(self) -> None:  # pragma: no cover - overridden
        while True:
            self.beat()
            await asyncio.sleep(10)

    def card(self) -> dict:
        return {"name": self.name}

    def decisions(self, limit: int = 20) -> list[dict]:
        return []
