"""STEP 13 #5: freeze authority set -> red at layer 2, layers 3-8 'not checked'. Plus the raw-sample mappings."""
import copy

from desk import fields, sources
from desk.agents import auditor as aud
from desk.tests.conftest import raw, run

MINT = "GTTQWTy96Vr2NNqrUHjg854AYKKP51jQr2Fcies7pump"


def test_freeze_authority_red_at_layer_2(monkeypatch):
    info = copy.deepcopy(raw("rpc_getAccountInfo.json")["result"])
    info["value"]["data"]["parsed"]["info"]["freezeAuthority"] = "FrzA1111111111111111111111111111111111111111"

    async def mint_info(m):
        return info
    monkeypatch.setattr(sources, "mint_info", mint_info)
    out = run(aud.Auditor().audit({"mint": MINT, "pool": "Gnh9QAHHkYP3mVe8pzqkL4YHUCBbDHwfs4iBp3wkNztd"}))
    L = out["layers"]
    assert L[0]["pass"] is True and L[0]["value"] == "Revoked"
    assert L[1]["pass"] is False and L[1]["value"] == "freeze not revoked"
    assert [x["value"] for x in L[2:]] == ["not checked"] * 6 and all(x["pass"] is None for x in L[2:])
    assert out["passed"] is False and out["reason"] == "freeze not revoked" and out["layersChecked"] == 2


def test_source_down_is_red_no_data(monkeypatch):
    async def boom(m):
        raise sources.SourceError("timeout")
    monkeypatch.setattr(sources, "mint_info", boom)
    from desk import config
    config.set_value("auditor.retry_gap_s", 0.5, "test")
    config.set_value("auditor.retries", 1, "test")
    out = run(aud.Auditor().audit({"mint": MINT}))
    assert out["layers"][0]["value"] == "no data: mint" and out["passed"] is False


def test_layer7_groups_from_raw_graph():
    g = raw("rugcheck_insiders_graph.json")
    n = aud.biggest_group(g)
    assert isinstance(n, int) and n >= 1
    assert aud.biggest_group([]) == 0          # RugCheck found no linked wallets
    assert aud.biggest_group({"error": "x"}) is None
    assert aud.biggest_group([{"nodes": []}]) is None   # no edges key = no data


def test_layer8_raw_gpa_counts_holders():
    accs = raw("gpa_TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb.json")["result"]
    held = [a for a in accs if int(fields.get("rpc_token_account", "amount", a)) > 0]
    assert len(accs) == 2464 and len(held) == 354
    assert not any(fields.get("rpc_token_account", "delegate", a) for a in held)


def test_layer5_mapping_on_raw_report():
    rep = raw("rugcheck_report.json")
    m = fields.get("rugcheck_report", "markets", rep)[0]
    assert fields.get("rugcheck_report", "market_pool", m) == "Gnh9QAHHkYP3mVe8pzqkL4YHUCBbDHwfs4iBp3wkNztd"
    assert fields.num("rugcheck_report", "market_lp_locked_pct", m) == 100
    assert fields.get("rugcheck_report", "creator", rep) == "BknCg64tRrKjBCz82WSNXy5gb2LzYbBtsAHuADerxtoa"


def test_pumpportal_message_mapping():
    msg = [m for m in raw("pumpportal_migration.json") if "mint" in m][0]
    assert fields.get("pumpportal", "mint", msg).endswith("pump")
    assert fields.get("pumpportal", "pool_type", msg) == "pump-amm"
    assert fields.get("pumpportal", "creator", msg) is None and fields.get("pumpportal", "pool", msg) is None
