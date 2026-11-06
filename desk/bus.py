"""Queues between agents (scanner -> auditor -> analyst -> executor -> risk) and the live event hub for SSE."""
from __future__ import annotations

import asyncio
import json
import time

PIPELINE = ("auditor", "analyst", "executor", "risk")

queues: dict[str, asyncio.Queue] = {}
_subs: set[asyncio.Queue] = set()


def q(name: str) -> asyncio.Queue:
    if name not in queues:
        queues[name] = asyncio.Queue()
    return queues[name]


async def send(name: str, msg: dict) -> None:
    await q(name).put(msg)


def subscribe() -> asyncio.Queue:
    sq: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subs.add(sq)
    return sq


def unsubscribe(sq: asyncio.Queue) -> None:
    _subs.discard(sq)


def publish(kind: str, data: dict) -> None:
    """Fan an event out to every open app stream (SSE kinds: scan, audit_layer, verdict, signal, fill, exit, balance...)."""
    payload = json.dumps({"kind": kind, "ts": time.time(), "data": data}, default=str)
    for sq in list(_subs):
        try:
            sq.put_nowait(payload)
        except asyncio.QueueFull:
            pass
