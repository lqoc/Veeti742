"""STEP 13 #6 test_replay (offline part): one graduation runs the whole PAPER pipeline end to end,
Scanner -> Auditor (8 layers) -> Analyst (rules) -> Executor -> Risk, with sources answering in the exact
shapes of logs/raw/. The live 2-hour PAPER run is `python -m desk.main` itself; see README."""
import asyncio
import copy
import time

from desk import bus, config, db, registry, sources
from desk.tests.conftest import pair, raw, run

MINT = "REPLmint111111111111111111111111111111pump"
POOL = "Gnh9QAHHkYP3mVe8pzqkL4YHUCBbDHwfs4iBp3wkNztd"
CREATOR = "BknCg64tRrKjBCz82WSNXy5gb2LzYbBtsAHuADerxtoa"


def install(monkeypatch, price_path):
    info = copy.deepcopy(raw("rpc_getAccountInfo.json")["result"])
    rep = copy.deepcopy(raw("rugcheck_report.json"))
    accs = raw("gpa_TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb.json")["result"]
    state = {"i": 0}

    async def dex(mints):
        px = price_path[min(state["i"], len(price_path) - 1)]
        return {MINT: pair(MINT, px, liq=50_000, v1=40_000, b1=220, p5=-6.0, b5=30, s5=12, p1=10.0, pool=POOL,
                           name="Replay", symbol="RPLY")}

    async def mint_info(m):
        return info

    async def supply(m):
        return 1_000_000_000_000_000, 6

    async def top10(m, pool, sup):
        return 16.8, "largest"

    async def owner_balance(owner, m):
        return 9_000_000_000_000      # 0.9% and unchanged since migration

    async def report(m):
        return rep

    async def graph(m):
        return []

    async def gpa(m):
        return accs

    for name, fn in (("dex_tokens", dex), ("mint_info", mint_info), ("token_supply", supply), ("top10_pct", top10),
                     ("owner_balance", owner_balance), ("rug_report", report), ("rug_graph", graph),
                     ("token_accounts", gpa)):
        monkeypatch.setattr(sources, name, fn)
    return state


def test_graduation_to_trailed_exit(monkeypatch):
    path = [0.0010, 0.0010, 0.0012, 0.0013, 0.00112]   # +30% peak, then 13.8% off the top -> trail
    st = install(monkeypatch, path)
    pushes = []

    async def fake_push(line, mint=None, url=None, trade=True, title="Grok Bot"):
        pushes.append(line)
        return {"url": url}
    for mod in ("executor", "risk", "auditor", "analyst"):
        monkeypatch.setattr(f"desk.agents.{mod}.notifier.push", fake_push)
    config.set_value("scanner.min_pool_age_min", 0, "test")
    config.set_value("auditor.min_pool_age_min", 0, "test")
    agents = registry.build()

    async def go():
        sc = agents["scanner"]
        await sc.on_migration({"signature": "sig", "mint": MINT, "txType": "migrate", "pool": "pump-amm"})
        db.execute("UPDATE coins SET creator=?, creator_balance_at_migration=? WHERE mint=?",
                   (CREATOR, "9000000000000", MINT))
        await asyncio.sleep(0)
        await sc.poll_once()
        await agents["auditor"].handle(await bus.q("auditor").get())
        m = await bus.q("analyst").get()
        await agents["analyst"].handle(m["scan"], m["audit"])
        await agents["executor"].handle(await bus.q("executor").get())
        r = agents["risk"]
        for i in range(1, len(path)):
            st["i"] = i
            await r.poll_once()
            await asyncio.sleep(0.01)
    run(go())
    acts = db.query("SELECT kind, title, subtitle, value FROM activity ORDER BY id")
    kinds = [a["kind"] for a in acts]
    assert kinds == ["bought", "sold"], acts
    assert acts[1]["subtitle"] == "Risk · trailing stop"
    p = db.one("SELECT * FROM positions WHERE mint=?", (MINT,))
    assert p["exit_reason"] == "trail" and p["pnl_usd"] > 0 and p["mode"] == "PAPER"
    layers = db.query("SELECT layer, value_text, pass FROM audits WHERE mint=? ORDER BY layer", (MINT,))
    assert [l["pass"] for l in layers] == [1] * 8
    assert [l["value_text"] for l in layers][:4] == ["Revoked", "Revoked", "0.9% · sold 0", "16.8%"]
    assert layers[4]["value_text"] == "100% burned" and layers[6]["value_text"] == "None"
    assert layers[7]["value_text"] == "None"
    assert db.scalar("SELECT holders FROM coins WHERE mint=?", (MINT,)) == 354
    assert any(x.startswith("Bought Replay · $4.00") for x in pushes)      # score 98 -> $4 tier
    assert any(x.startswith("Sold Replay · +$") for x in pushes)
