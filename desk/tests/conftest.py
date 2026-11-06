import asyncio
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("DESK_LOGS", tempfile.mkdtemp(prefix="desk-test-logs-"))  # keep test runs out of desk/logs

import pytest

from desk import book, bus, config, db, registry, vault

RAW = Path(__file__).resolve().parent.parent / "logs" / "raw"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    """Every test gets its own data dir, database, secrets and agents."""
    monkeypatch.setattr(db, "DATA", tmp_path)
    db.connect(tmp_path / "desk.db")
    vault._cache = None
    config._values.clear()
    config._listeners.clear()
    config.load()
    registry.AGENTS.clear()
    bus.queues.clear()
    book._wallet_cache.clear()
    registry.build()
    book.start_run()
    db.state_set("night_start", 0)
    yield tmp_path


def raw(name):
    return json.loads((RAW / name).read_text())


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def pair(mint, price, liq=50_000, v1=30_000, b1=200, s1=150, b5=20, s5=15, p5=1.0, p1=20.0, p24=20.0,
         name="Test", symbol="TEST", pool="POOL1111111111111111111111111111111111111111"):
    """A DexScreener pair with the exact keys of logs/raw/dexscreener_pair.json."""
    return {"chainId": "solana", "dexId": "pumpswap", "pairAddress": pool,
            "baseToken": {"address": mint, "name": name, "symbol": symbol},
            "quoteToken": {"address": "So11111111111111111111111111111111111111112"},
            "priceUsd": str(price), "txns": {"m5": {"buys": b5, "sells": s5}, "h1": {"buys": b1, "sells": s1}},
            "volume": {"m5": 1000.0, "h1": v1, "h24": v1}, "priceChange": {"m5": p5, "h1": p1, "h24": p24},
            "liquidity": {"usd": liq}, "marketCap": 100_000, "fdv": 100_000, "pairCreatedAt": 1793941287000}
