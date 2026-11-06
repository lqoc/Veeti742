"""STEP 13 #1 test_links + #1b test_notify."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from desk import api, links, notifier, report, vault
from desk.tests.conftest import run

REPO = Path(__file__).resolve().parents[2]
MINT = "GTTQWTy96Vr2NNqrUHjg854AYKKP51jQr2Fcies7pump"
FOMO = re.compile(r"https?://(?:www\.)?fomo\.family[^\s\"'<>)]*")
SHORTENERS = re.compile(r"(bit\.ly|t\.co/|tinyurl|goo\.gl|ow\.ly|is\.gd|cutt\.ly|rebrand\.ly)")


def ok_url(u: str) -> bool:
    return "r=savipww" in u or u.endswith("/r/savipww")


def test_coin_and_profile_urls():
    u = links.coin_url(MINT)
    assert u == f"https://fomo.family/coin?address={MINT}&chainId=1399811149&r=savipww&source=share_link"
    assert links.coin_url(MINT, "base").endswith("chainId=8453&r=savipww&source=share_link")
    assert links.profile_url("@veeti") == "https://fomo.family/profile/veeti?source=share_link&r=savipww"
    assert links.coin_url(MINT, "ethereum") == links.SIGNUP_URL == "https://fomo.family/r/savipww"


def test_no_fomo_url_built_outside_links_py():
    offenders = []
    for p in (REPO / "desk").rglob("*"):
        if p.suffix not in (".py", ".js", ".html", ".css", ".json") or "logs" in p.parts or "tests" in p.parts:
            continue
        if p.name == "links.py":
            continue
        text = p.read_text(errors="ignore")
        if "fomo.family" in text:
            offenders.append(str(p))
    assert offenders == []


def test_no_shortened_links_anywhere():
    for p in (REPO / "desk").rglob("*"):
        if p.suffix in (".py", ".js", ".html", ".css") and "tests" not in p.parts:
            assert not SHORTENERS.search(p.read_text(errors="ignore")), p


def _seed():
    from desk import db
    db.execute("INSERT INTO coins(mint, name, symbol, pool, migrated_at, state, first_seen) VALUES(?,?,?,?,?,?,?)",
               (MINT, "LUNOX", "LUNOX", "POOL", 0, "passed", 1))
    db.execute("INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value) VALUES(1,'rejected',?,'auditor',"
               "'Rejected LUNOX','Auditor · dev sold','Skipped')", (MINT,))
    db.execute("INSERT INTO audits(mint, ts, layer, name, value_text, pass, source, run_id) VALUES(?,1,1,'Mint authority',"
               "'Revoked',1,'rpc','r1')", (MINT,))
    db.execute("INSERT INTO positions(mint, opened_at, entry_price, size_usd, peak_price, stop_price, mode, name, symbol,"
               " stop_pct, status, venue, last_price) VALUES(?,1,1.0,2.0,1.0,0.94,'PAPER','LUNOX','LUNOX',6,'closed',"
               "'wallet',1.1)", (MINT,))
    db.execute("UPDATE positions SET closed_at=2, exit_price=1.1, pnl_usd=0.2, exit_reason='trail'")


def test_every_fomo_url_in_api_pushes_and_page_has_ref(monkeypatch):
    _seed()
    from desk.config import set_value
    set_value("desk.fomo_handle", "veeti")

    async def no_dex(mints):
        return {}
    monkeypatch.setattr("desk.sources.dex_tokens", no_dex)
    c = TestClient(api.app)
    h = {"Authorization": "Bearer " + vault.get("APP_TOKEN")}
    blobs = []
    for path in ("/api/state", "/api/activity", f"/api/coin/{MINT}", f"/api/audit/{MINT}", "/api/account",
                 "/api/agents/risk/decisions", "/api/search?q=LUNOX"):
        r = c.get(path, headers=h)
        assert r.status_code == 200, path
        blobs.append(r.text)
    r = c.post("/api/command", headers=h, json={"text": "why LUNOX"})
    blobs.append(r.text)
    r = c.post("/api/command", headers=h, json={"text": "invite"})
    blobs.append(r.text)
    pushed = run(notifier.push("Sold LUNOX · +$0.20 (+10%)", mint=MINT))
    blobs.append(json.dumps(pushed))
    _rid, page = report.build(0)
    blobs.append(page)
    found = [u for b in blobs for u in FOMO.findall(b.replace("&amp;", "&"))]
    assert found, "expected FOMO links in responses"
    bad = [u for u in found if not ok_url(u)]
    assert bad == []
    assert not any(SHORTENERS.search(b) for b in blobs)
    assert links.SIGNUP_URL in page and "New to FOMO? " in page


def test_notify_paper_push_carries_exact_links_url():
    """1b: one PAPER push through every channel set up (in-app bell + web push)."""
    res = run(notifier.push("Bought LUNOX · $2.00 · 1% of stack", mint=MINT))
    assert res["body"].startswith("PAPER · ")
    assert res["url"] == links.coin_url(MINT, "solana")
    assert ok_url(res["url"])
    assert res["webpush"] == {"subs": 0, "sent": 0, "failed": 0}  # no iPhone subscribed in the test db
    from desk import db
    row = db.one("SELECT url FROM notifications WHERE id=?", (res["id"],))
    assert row["url"] == links.coin_url(MINT, "solana")
