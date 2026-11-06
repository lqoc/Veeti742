"""STEP 13 #2 test_gate: thresholds in jev and rules mode."""
import pytest

from desk import bus, config, db, sources
from desk.agents import analyst
from desk.tests.conftest import pair, run

AT = {"alive_6h": 0.70, "organic": 0.65, "flush": 0.75, "chase": 0.30}   # score exactly 70


@pytest.fixture
def min70():
    config.set_value("analyst.min_score", 70, "test")


def test_all_at_threshold_pass(min70):
    score, failed = analyst.gate(AT)
    assert score == 70 and failed == []


@pytest.mark.parametrize("key,val,rule", [("alive_6h", 0.69, "alive 0.69"), ("organic", 0.64, "organic 0.64"),
                                          ("chase", 0.31, "chase 0.31")])
def test_each_rule_rejects(min70, key, val, rule):
    a = dict(AT, flush=1.0)
    a[key] = val
    score, failed = analyst.gate(a)
    assert failed[0] == rule


def test_score_69_rejects(min70):
    score, failed = analyst.gate(dict(AT, flush=0.71))
    assert score == 69 and failed == ["score 69"]


def test_default_min_score_is_52_for_the_tiers():
    assert config.get("analyst.min_score") == 52


def _scan(mint):
    db.execute("INSERT INTO coins(mint, name, symbol, migrated_at, state, first_seen) VALUES(?,?,?,?,?,?)",
               (mint, "Rulecoin", "RULE", 0, "passed", 1))
    return {"mint": mint, "name": "Rulecoin", "symbol": "RULE", "ageMin": 20, "liquidityUsd": 50_000}


def test_rules_mode_with_fake_dexscreener(monkeypatch, min70):
    mint = "RULEmint111111111111111111111111111111pump"
    # alive = 20000/25000 = 0.80, organic = 100/150 = 0.67, flush 0 (p5 > 0), chase = 20/80 = 0.25 -> score 55
    fake = pair(mint, 0.001, v1=20_000, b1=100, p5=2.0, p1=20.0)

    async def dex(mints):
        return {mint: fake}
    monkeypatch.setattr(sources, "dex_tokens", dex)
    a = analyst.Analyst()
    out = run(a.handle(_scan(mint), {"extra": {"bundled": 0}}))
    assert out["model"] == "rules" and out["inputTokens"] == 0
    assert out["answers"] == {"alive_6h": 0.8, "organic": 0.6667, "flush": 0.0, "chase": 0.25}
    assert out["score"] == 55 and out["failed"] == ["score 55"]
    row = db.one("SELECT subtitle FROM activity WHERE mint=?", (mint,))
    assert row["subtitle"] == "Analyst (rules) · score 55"


def test_rules_bundled_penalty_and_flush(monkeypatch):
    v = {"v1": 50_000, "b1": 300, "p5": -6.0, "b5": 30, "s5": 20, "p1": 10.0}
    a = analyst.rules_answers(v, bundled=3)
    assert a["organic"] == 0.5 and a["flush"] == 1.0 and a["alive_6h"] == 1.0 and a["chase"] == 0.125
    assert analyst.rules_answers(dict(v, p5=-1.0), 0)["flush"] == 0.5


def test_rules_pass_goes_to_executor(monkeypatch):
    mint = "PASSmint111111111111111111111111111111pump"
    fake = pair(mint, 0.001, v1=40_000, b1=200, p5=-6.0, b5=30, s5=10, p1=8.0)

    async def dex(mints):
        return {mint: fake}
    monkeypatch.setattr(sources, "dex_tokens", dex)
    out = run(analyst.Analyst().handle(_scan(mint), {"extra": {"bundled": 0}}))
    assert out["passed"] and out["score"] == 98
    assert bus.q("executor").qsize() == 1


def test_never_reasks_same_coin(monkeypatch):
    mint = "ONCEmint111111111111111111111111111111pump"

    async def dex(mints):
        return {mint: pair(mint, 0.001)}
    monkeypatch.setattr(sources, "dex_tokens", dex)
    a = analyst.Analyst()
    assert run(a.handle(_scan(mint), {})) is not None
    assert run(a.handle({"mint": mint}, {})) is None


def test_state_text_has_no_thresholds_or_buy():
    s = analyst.build_state({"name": "X", "ageMin": 12}, {"extra": {"top10": 16.8, "dev_pct": 0.9, "lp": "100% burned",
                                                                   "bundled": 0, "delegations": "None"}},
                            {"liq": 50000, "mcap": 90000, "b5": 1, "s5": 2, "v5": 3, "p5": 4, "b1": 5, "s1": 6,
                             "v1": 7, "p1": 8})
    assert "buy " not in s.lower().replace("buys", "") and "0.70" not in s and "70" not in s.split("Liquidity")[0]
    assert len(s) <= 3000
