"""STEP 13 #4 test_executor + the $0.50-$4 score tiers, 3-position cap and $1 USDC reserve."""
import time

import pytest

from desk import book, config, db, sources
from desk.agents import executor as ex
from desk.tests.conftest import pair, run


@pytest.fixture
def pct_mode():
    config.set_value("executor.size_mode", "pct", "test")


def test_stack_200_bump_sends_47_50(pct_mode):
    s = ex.size_for(80, 1_000_000, 200, 200)
    assert s["raw"] == 16 and s["size"] == 47.50 and "bumped" in s["caps"]


def test_stack_200_skip_skips(pct_mode):
    config.set_value("executor.below_min", "skip", "test")
    s = ex.size_for(80, 1_000_000, 200, 200)
    assert s["size"] == 0 and s["skip"] == "under $47.50"


def test_paper_start_200_with_skip_warns(pct_mode):
    config.set_value("executor.below_min", "skip", "test")
    w = config.ticket_check(200)
    assert w["level"] == "warn" and w["text"].startswith("every coin will be skipped, stack under $593.75")
    config.set_value("executor.below_min", "bump", "test")
    assert config.ticket_check(200)["text"] == "tickets bumped to $47.50 = 23.8% of stack"


def test_pool_40k_caps_at_800(pct_mode):
    s = ex.size_for(80, 40_000, 100_000, 100_000)
    assert s["size"] == 800 and "pool" in s["caps"]


@pytest.mark.parametrize("score,usd", [(52, 0.50), (59, 0.50), (60, 1.0), (69, 1.0), (70, 2.0), (79, 2.0),
                                       (80, 3.0), (89, 3.0), (90, 4.0), (100, 4.0)])
def test_score_tiers(score, usd):
    assert ex.size_for(score, 50_000, 200, 200)["size"] == usd


def test_below_tiers_skips():
    assert ex.size_for(51, 50_000, 200, 200)["skip"] == "score 51 under tiers"


def test_keeps_last_dollar_of_usdc(monkeypatch):
    config.set_value("desk.mode", "LIVE", "test")
    book._wallet_cache.update({"wallet": "W", "usdc": 1.40, "sol": 0.05, "sol_usd": 4.0, "lamports": 50_000_000})
    free = book.free_cash()
    assert abs(free - 0.40) < 1e-9
    s = ex.size_for(55, 50_000, 5.4, free)
    assert s["size"] == 0 and "keeps last $1.00 USDC" in s["skip"]
    assert ex.size_for(55, 50_000, 5.4, 1.60 - 1.0)["size"] == 0.50


def test_max_three_positions(monkeypatch):
    for i in range(3):
        db.execute("INSERT INTO positions(mint, opened_at, entry_price, size_usd, peak_price, stop_price, mode, status, venue)"
                   " VALUES(?,?,1,1,1,0.94,'PAPER','open','wallet')", (f"M{i}", time.time()))
    e = ex.Executor()
    out = run(e.handle({"scan": {"mint": "NEW", "name": "New"}, "audit": {}, "analyst": {"score": 90}}))
    assert out["status"] == "skipped" and out["reason"] == "3 positions open"


def test_paper_fill_opens_position_and_pushes(monkeypatch):
    mint = "FILLmint111111111111111111111111111111pump"
    db.execute("INSERT INTO coins(mint, name, symbol, migrated_at, state, first_seen) VALUES(?,?,?,?,?,?)",
               (mint, "Fill", "FILL", 0, "passed", 1))

    async def dex(mints):
        return {mint: pair(mint, 0.002, liq=60_000)}
    monkeypatch.setattr(sources, "dex_tokens", dex)
    pushes = []

    async def fake_push(line, mint=None, url=None, trade=True, title="Grok Bot"):
        pushes.append((line, mint))
        return {"url": url}
    monkeypatch.setattr("desk.agents.executor.notifier.push", fake_push)
    e = ex.Executor()
    out = run(e.handle({"scan": {"mint": mint, "name": "Fill", "liquidityUsd": 60_000},
                        "audit": {"layersChecked": 8}, "analyst": {"score": 74}, "price": 0.002}))
    assert out["status"] == "bought" and out["sizeUsd"] == 2.0 and "r=savipww" in out["link"]
    p = db.one("SELECT * FROM positions WHERE mint=?", (mint,))
    assert p["status"] == "open" and p["entry_price"] == 0.002 and p["stop_pct"] == 6
    assert pushes[0][0].startswith("Bought Fill · $2.00")
    assert abs(book.paper_cash() - (200 - 2.0 - config.get("wallet.paper_fee_usd"))) < 1e-9
    # 1 push per coin per night: a second analyst pass is ignored
    assert run(e.handle({"scan": {"mint": mint, "name": "Fill"}, "audit": {}, "analyst": {"score": 90}})) is None


def test_recheck_skips(monkeypatch):
    mint = "RUNmint1111111111111111111111111111111pump"

    async def dex(mints):
        return {mint: pair(mint, 0.0012, liq=60_000)}
    monkeypatch.setattr(sources, "dex_tokens", dex)
    out = run(ex.Executor().handle({"scan": {"mint": mint, "name": "Run", "liquidityUsd": 60_000},
                                    "audit": {}, "analyst": {"score": 80}, "price": 0.001}))
    assert out["reason"] == "ran before the push"

    async def dex2(mints):
        return {mint: pair(mint, 0.001, liq=40_000)}
    monkeypatch.setattr(sources, "dex_tokens", dex2)
    out = run(ex.Executor().handle({"scan": {"mint": "X" + mint[1:], "name": "Liq", "liquidityUsd": 60_000},
                                    "audit": {}, "analyst": {"score": 80}, "price": 0.001}))
    assert out["status"] == "skipped"
