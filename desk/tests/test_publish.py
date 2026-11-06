"""deed.page website: what gets published, that nothing secret rides along, and the publish protocol."""
import io
import json
import tarfile

import httpx

from desk import config, db, publish, vault
from desk.tests.conftest import run

MINT = "PUBLmint111111111111111111111111111111pump"


def seed():
    db.execute("INSERT INTO coins(mint, name, symbol, migrated_at, state, first_seen) VALUES(?,?,?,?,?,strftime('%s','now'))",
               (MINT, "Pub", "PUB", 0, "killed"))
    db.execute("INSERT INTO scans(mint, ts, liquidity_usd, verdict, reason, snapshot) VALUES(?,strftime('%s','now'),20000,"
               "'kill','liq $20.0K',?)", (MINT, json.dumps({"price": 0.001, "liq": 20000, "name": "Pub"})))
    db.execute("INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value) VALUES(1,'rejected',?,'scanner',"
               "'Rejected Pub','Scanner · liq $20.0K','Skipped')", (MINT,))


def untar(body: bytes) -> dict[str, bytes]:
    out = {}
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tf:
        for m in tf.getmembers():
            out[m.name] = tf.extractfile(m).read()
    return out


def test_shell_has_no_wallet_import_form():
    shell = publish.shell_files()
    assert "index.html" in shell and "app.js" in shell and "manifest.json" in shell
    js = shell["app.js"].decode()
    assert "WALLET-IMPORT" not in js and "private key" not in js.lower() and "/api/wallet/import" not in js
    assert "staticApi" in js          # the website mode is there


def test_data_files_are_clean(monkeypatch):
    seed()
    config.set_value("desk.rpc_url", "https://mainnet.helius-rpc.com/?api-key=SECRETKEY123", "test")
    vault.set_secret("DEEDPAGE_TOKEN", "deed_tok_" + "s" * 30)
    vault.set_secret("TYPESAFE_API_KEY", "tsk_" + "k" * 30)
    files = run(publish.data_files(with_export=True))
    d = f"d/{vault.data_path()}/"
    assert all(k.startswith(d) for k in files)
    for name in ("state.json", "chart-LIVE.json", "activity.json", "account.json", "settings.json", "coins.json",
                 "export.json", "meta.json", f"coin/{MINT}.json", f"audit/{MINT}.json", "decisions-risk.json"):
        assert d + name in files, name
    blob = b"\n".join(files.values()).decode()
    for secret in vault.secret_values() + ["SECRETKEY123"]:
        assert secret not in blob
    assert '"public_url"' not in blob and '"vapid_public"' not in blob
    for u in [x for x in blob.replace("\\u0026", "&").split('"') if x.startswith("https://fomo.family")]:
        assert "r=savipww" in u or u.endswith("/r/savipww")
    assert vault.data_path() not in blob        # the secret folder name is a path, never inside the data


def test_first_publish_saves_token_then_merges(monkeypatch):
    seed()
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if len(calls) == 1:
            assert "authorization" not in req.headers
            return httpx.Response(200, json={"ok": True, "url": "https://grokbot-test.deed.page/", "slug": "grokbot-test",
                                             "version": "v1", "token": "deed_tok_" + "t" * 32})
        assert req.headers["authorization"] == "Bearer deed_tok_" + "t" * 32
        return httpx.Response(200, json={"ok": True, "url": "https://grokbot-test.deed.page/", "version": f"v{len(calls)}"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pub = publish.Publisher()

    async def go():
        await pub.once(client)
        await pub.once(client)
    run(go())
    assert vault.get("DEEDPAGE_TOKEN") == "deed_tok_" + "t" * 32
    first, second = untar(calls[0].content), untar(calls[1].content)
    assert "index.html" in first and "app.js" in first                 # full publish carries the shell
    assert "index.html" not in second and calls[1].headers.get("x-merge") == "1"   # then data only
    assert calls[0].url.params["slug"] == config.get("desk.site_slug")
    assert publish.login_link() == f"https://grokbot-test.deed.page/#d={vault.data_path()}"
    for body in (calls[0].content, calls[1].content):
        assert b"deed_tok_" not in body and vault.get("APP_TOKEN").encode() not in body


def test_slug_taken_picks_suggestion():
    seq = []

    def handler(req):
        seq.append(req.url.params["slug"])
        if len(seq) == 1:
            return httpx.Response(409, json={"ok": False, "error": "slug_taken", "suggestion": "grokbot-free1"})
        return httpx.Response(200, json={"ok": True, "url": "https://grokbot-free1.deed.page/", "token": "deed_tok_x"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    run(publish.put(b"x", merge=False, client=client))
    assert seq[1] == "grokbot-free1" and config.get("desk.site_slug") == "grokbot-free1"


def test_rate_limit_reports_wait():
    def handler(req):
        return httpx.Response(429, json={"ok": False, "error": "rate_limited", "retry_after_seconds": 900,
                                         "message_for_user": "Hobby $3/mo lifts this."})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        run(publish.put(b"x", merge=True, client=client))
        assert False
    except publish.PublishError as e:
        assert str(e).split("|")[2] == "900"
