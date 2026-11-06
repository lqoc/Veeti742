"""Results export: every coin with its stage, reason, what the agents saw, and what the price did afterwards."""
import json
import time

from fastapi.testclient import TestClient

from desk import api, db, learn, sources, vault
from desk.tests.conftest import pair, run


def seed(mint, first_seen, **scan):
    db.execute("INSERT INTO coins(mint, name, symbol, migrated_at, state, first_seen) VALUES(?,?,?,?,?,?)",
               (mint, mint[:4], mint[:4], first_seen, scan.pop("state", "killed"), first_seen))
    if scan:
        db.execute("INSERT INTO scans(mint, ts, liquidity_usd, top10_pct, h1_change_pct, age_min, verdict, reason, snapshot) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (mint, first_seen + 600, scan["liq"], 10, 50, 10, scan["verdict"],
                                                 scan.get("reason"), json.dumps({"price": 1.0, "liq": scan["liq"]})))


def follow(mint, t0, path):
    for i, (dt, px, liq) in enumerate(path):
        db.execute("INSERT INTO followups(mint, ts, price, liq, mcap, volume_m5) VALUES(?,?,?,?,?,?)",
                   (mint, t0 + dt, px, liq, None, None))


def test_outcome_horizons_and_rug():
    t = time.time() - 7 * 3600
    seed("KILLmint", t, liq=20_000, verdict="kill", reason="liq $20.0K")
    t0 = t + 600
    follow("KILLmint", t0, [(0, 1.0, 20_000), (900, 1.5, 22_000), (3600, 3.0, 40_000), (3 * 3600, 2.0, 30_000),
                            (6 * 3600, 0.5, 2_000)])
    seed("RUGmint1", t, liq=50_000, verdict="pass")
    db.execute("INSERT INTO audits(mint, ts, layer, name, value_text, pass, source, run_id) "
               "VALUES('RUGmint1', ?, 3, 'Dev wallet', 'dev sold', 0, 'rpc', 'r9')", (t0,))
    follow("RUGmint1", t0, [(0, 1.0, 50_000), (900, None, None)])
    out = learn.export(hours=24)
    rows = {r["mint"]: r for r in out["coins"]}
    k = rows["KILLmint"]
    assert k["stage"] == "scanner_kill" and k["reason"] == "liq $20.0K"
    o = k["outcome"]
    assert o["at_15m_pct"] == 50.0 and o["at_1h_pct"] == 200.0 and o["at_3h_pct"] == 100.0 and o["at_6h_pct"] == -50.0
    assert o["max_up_pct"] == 200.0 and o["rugged"] is True
    r = rows["RUGmint1"]
    assert r["stage"] == "audit_reject" and r["reason"] == "layer 3 · dev sold" and r["outcome"]["rugged"] is True
    st = out["summary"]["stages"]
    assert st["scanner_kill"]["n"] == 1 and st["scanner_kill"]["doubled_within_6h"] == 1
    assert st["scanner_kill"]["reasons"] == {"liq": 1}
    assert out["settings"]["scanner.min_liquidity_usd"] == 40000


def test_export_endpoint_and_followup_loop(monkeypatch):
    t = time.time() - 600
    seed("LIVEmint", t, liq=60_000, verdict="kill", reason="+120% in 1h")

    async def dex(mints):
        return {m: pair(m, 2.0, liq=55_000) for m in mints}
    monkeypatch.setattr(sources, "dex_tokens", dex)
    assert run(learn.followup_once()) == 1
    c = TestClient(api.app)
    assert c.get("/api/export").status_code == 401
    r = c.get("/api/export?hours=3", headers={"Authorization": "Bearer " + vault.get("APP_TOKEN")})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    body = r.json()
    row = body["coins"][0]
    assert row["mint"] == "LIVEmint" and row["outcome"]["samples"] >= 1 and "r=savipww" in row["link"]
    assert vault.get("APP_TOKEN") not in r.text


def test_export_command_returns_download_button():
    from desk import commands
    out = run(commands.handle("export 6h"))
    assert out["buttons"] == [{"label": "Download results", "export": 6.0}]
