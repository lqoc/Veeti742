"""STEP 13 #7: secret values never in web/, logs/, the repo, API responses or the Page; 401 without token."""
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from desk import api, report, vault, wallet
from desk.tests.conftest import run

REPO = Path(__file__).resolve().parents[2]
TEST_KEY = "4wBqpZM9xaSheZzJSMawUKKwhdpChKbZ5eu5ky4Vigw9NM5zK3yaL4cE8m8kVxqRv9X3eS7KDdBYn7J3XQ4gJxCY"  # random, unfunded


def tracked_text() -> str:
    try:
        files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO, capture_output=True,
                               text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):   # no git (e.g. inside the Docker image): walk the tree
        files = [str(p.relative_to(REPO)) for p in REPO.rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts]
    out = []
    for f in files:
        p = REPO / f
        if p.is_file() and p.suffix not in (".png", ".webp", ".db"):
            out.append(p.read_text(errors="ignore"))
    for p in (REPO / "desk" / "logs").rglob("*"):
        if p.is_file():
            out.append(p.read_text(errors="ignore"))
    return "\n".join(out)


def test_no_secret_in_repo_web_logs_api_page(monkeypatch):
    vault.set_secret("TYPESAFE_API_KEY", "tsk_test_" + "x" * 30)
    from solders.keypair import Keypair
    import base58
    kp = Keypair()
    secret_b58 = base58.b58encode(bytes(kp)).decode()
    c = TestClient(api.app)
    h = {"Authorization": "Bearer " + vault.get("APP_TOKEN")}

    async def no_rpc(*a, **k):
        from desk.sources import SourceError
        raise SourceError("offline")
    monkeypatch.setattr("desk.sources.rpc", no_rpc)
    r = c.post("/api/wallet/import", headers=h, json={"secret": secret_b58})
    assert r.status_code == 200 and r.json()["pubkey"] == str(kp.pubkey())
    blobs = [r.text]
    for path in ("/api/state", "/api/account", "/api/settings", "/api/wallet", "/api/notifications", "/api/activity"):
        blobs.append(c.get(path, headers=h).text)
    blobs.append(report.build(0)[1])
    blobs.append(tracked_text())
    blobs.append((wallet.db.DATA / "wallet.enc").read_text())          # encrypted, not the key itself
    everything = "\n".join(blobs)
    for v in vault.secret_values() + [secret_b58]:
        assert v not in everything
    assert "TYPESAFE_API_KEY" not in c.get("/api/account", headers=h).text


def test_401_without_token():
    c = TestClient(api.app)
    assert c.get("/api/state").status_code == 401
    assert c.get("/api/state", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/api/state", headers={"Authorization": "Bearer " + vault.get("APP_TOKEN")}).status_code == 200
    assert c.get("/api/stream").status_code == 401


def test_wallet_roundtrip_and_rejects_seed_phrase():
    from solders.keypair import Keypair
    kp = Keypair()
    pub = wallet.import_wallet(str(list(bytes(kp))).replace(" ", ""))
    wallet._kp = None
    assert pub == str(kp.pubkey()) and str(wallet.keypair().pubkey()) == pub
    try:
        wallet.parse_secret("legal winner thank year wave sausage worth useful legal winner thank yellow")
        assert False
    except wallet.WalletError as e:
        assert "recovery phrase" in str(e)
