"""STEP 13 #3 test_risk."""
import time

from fastapi.testclient import TestClient

from desk import api, book, commands, config, db, sources, vault
from desk.agents import risk as riskmod
from desk.tests.conftest import pair, run

MINT = "RISKmint111111111111111111111111111111pump"


def pos(entry=1.0, stop=6, opened=None, peak=None, trail=0):
    return {"entry_price": entry, "stop_price": entry * (1 - stop / 100), "peak_price": peak or entry,
            "trail_active": trail, "opened_at": opened or time.time()}


def test_trail_exit_after_25_then_13_off_top():
    p = pos()
    r, peak, trail = riskmod.check(p, 1.25)
    assert r is None and trail and peak == 1.25
    p.update(peak_price=peak, trail_active=trail)
    r, _, _ = riskmod.check(p, 1.25 * 0.87)
    assert r == "trail"


def test_stop_at_minus_6_1():
    assert riskmod.check(pos(), 0.939)[0] == "stop"
    assert riskmod.check(pos(), 0.941)[0] is None


def test_timeout_3h_at_plus_4():
    assert riskmod.check(pos(opened=time.time() - 3 * 3600 - 1), 1.04)[0] == "timeout"
    assert riskmod.check(pos(opened=time.time() - 3 * 3600 - 1), 1.06)[0] is None


def open_paper(mint=MINT, price=1.0):
    from desk import registry
    ex = registry.get("executor")
    db.execute("INSERT OR IGNORE INTO coins(mint, name, symbol, migrated_at, state, first_seen) VALUES(?,?,?,?,?,?)",
               (mint, "Riskcoin", "RISK", 0, "passed", 1))
    sid = db.execute("INSERT INTO signals(mint, ts, price, size_usd, mode, status) VALUES(?,?,?,?,?,?)",
                     (mint, time.time(), price, 2.0, "PAPER", "sent"))
    return ex.open_position(sid, mint, {}, price, 2.0, 0.01, 75, "wallet")


def test_loosening_never_moves_open_stop_down():
    from desk import registry
    registry.get("risk")
    pid = open_paper()
    before = db.one("SELECT stop_price, stop_pct FROM positions WHERE id=?", (pid,))
    config.set_value("risk.stop_pct", 8, "test")       # looser
    after = db.one("SELECT stop_price, stop_pct FROM positions WHERE id=?", (pid,))
    assert after == before and after["stop_pct"] == 6
    config.set_value("risk.stop_pct", 4, "test")       # tighter -> applies
    tight = db.one("SELECT stop_price, stop_pct FROM positions WHERE id=?", (pid,))
    assert abs(tight["stop_price"] - 0.96) < 1e-9 and tight["stop_pct"] == 4


def test_set_stop_minus_8_then_drop_8_1(monkeypatch):
    from desk import registry
    r = registry.get("risk")
    old = open_paper("OLD" + MINT[3:], 1.0)              # opened at -6%
    assert run(commands.handle("set stop to -8%"))["text"] == "stop -6% -> -8%"
    new = open_paper(MINT, 1.0)
    prices = {MINT: pair(MINT, 0.919), "OLD" + MINT[3:]: pair("OLD" + MINT[3:], 0.95)}

    async def dex(mints):
        return {m: prices[m] for m in mints if m in prices}
    monkeypatch.setattr(sources, "dex_tokens", dex)
    pushes = []

    async def fake_push(line, mint=None, url=None, trade=True, title="Grok Bot"):
        pushes.append(line)
        return {}
    monkeypatch.setattr("desk.agents.risk.notifier.push", fake_push)

    async def go():
        await r.poll_once()
        for _ in range(20):
            if not r.exiting:
                break
            import asyncio
            await asyncio.sleep(0.05)
    run(go())
    p = db.one("SELECT * FROM positions WHERE id=?", (new,))
    assert p["status"] == "closed" and p["exit_reason"] == "stop" and p["stop_pct"] == 8
    assert any(x.startswith("Riskcoin hit the -8% stop · -$") for x in pushes)
    act = db.one("SELECT subtitle FROM activity WHERE ref_id=? AND kind='stopped'", (new,))
    assert act["subtitle"] == "Risk · -8% stop"
    c = TestClient(api.app)
    h = {"Authorization": "Bearer " + vault.get("APP_TOKEN")}
    marks = c.get("/api/chart?range=1D", headers=h).json()["marks"]
    assert any(m["kind"] == "loss" and m["text"].startswith("-8% · -$") for m in marks)
    card = next(a for a in c.get("/api/state", headers=h).json()["agents"] if a["name"] == "risk")
    assert card["stop_pct"] == 8
    o = db.one("SELECT stop_pct, status FROM positions WHERE id=?", (old,))
    assert o["stop_pct"] == 6 and o["status"] == "open"     # -5% is above its -6% stop


def test_risk_cannot_pause_with_open_position():
    from desk import control
    open_paper()
    try:
        control.set_paused("risk", True)
        assert False
    except control.ControlError as e:
        assert "can't be paused" in str(e)


def test_no_price_four_polls_pushes_once(monkeypatch):
    from desk import registry
    r = registry.get("risk")
    open_paper()

    async def dex(mints):
        return {}
    monkeypatch.setattr(sources, "dex_tokens", dex)
    pushes = []

    async def fake_push(line, mint=None, url=None, trade=True, title="Grok Bot"):
        pushes.append(line)
        return {}
    monkeypatch.setattr("desk.agents.risk.notifier.push", fake_push)
    for _ in range(6):
        run(r.poll_once())
    assert pushes == ["Riskcoin: no price for 1 min"]


def test_paper_pnl_counts_fees_both_sides():
    from desk import registry
    r = registry.get("risk")
    cash0 = book.paper_cash()
    pid = open_paper()
    book.paper_cash_add(-(2.0 + 0.01))
    p = db.one("SELECT * FROM positions WHERE id=?", (pid,))
    run(r._exit(p, "trail", 1.5))
    q = db.one("SELECT * FROM positions WHERE id=?", (pid,))
    assert abs(q["pnl_usd"] - (3.0 - 2.0 - 0.02)) < 1e-9
    assert abs(book.paper_cash() - (cash0 - 2.01 + 3.0 - 0.01)) < 1e-9


def test_live_needs_a_full_paper_night():
    import pytest
    from desk import control
    db.execute("INSERT INTO reports(id, ts, mode, night_start, path) VALUES('short', ?, 'PAPER', ?, '')",
               (time.time(), time.time() - 1800))
    with pytest.raises(control.ControlError, match="full PAPER night"):
        run(control.set_mode("LIVE", "LIVE"))
    db.execute("INSERT INTO reports(id, ts, mode, night_start, path) VALUES('full', ?, 'PAPER', ?, '')",
               (time.time(), time.time() - 7 * 3600))
    with pytest.raises(control.ControlError, match="import a wallet"):
        run(control.set_mode("LIVE", "LIVE"))
    with pytest.raises(control.ControlError, match="type LIVE"):
        run(control.set_mode("LIVE", "live"))


def test_take_profit_exits_at_target():
    config.set_value("risk.take_profit_pct", 15, "test")
    assert riskmod.check(pos(), 1.149)[0] is None
    assert riskmod.check(pos(), 1.15)[0] == "target"
    assert riskmod.check(pos(), 0.89)[0] == "stop"     # stop still wins first


def test_sniper_strategy_settings_and_live_keeps_it():
    from desk import control
    changed = config.apply_preset("sniper", "test")
    assert config.strategy() == "sniper" and changed
    assert config.get("scanner.min_pool_age_min") == 1 and config.get("risk.take_profit_pct") == 15
    assert config.get("analyst.rules_window") == "m5" and config.get("risk.poll_s") == 5
    assert config.undo_presets() == []          # LIVE keeps a chosen strategy
    config.apply_preset("strict", "test")
    assert config.strategy() == "strict" and config.get("scanner.min_pool_age_min") == 10
    assert config.get("risk.take_profit_pct") == 0 and config.get("risk.stop_pct") == 6
