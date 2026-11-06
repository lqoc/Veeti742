"""Your own Solana wallet: import, encrypted storage, balances.

The secret key is encrypted with AES-256-GCM (key in secrets.json) and written to DESK_DATA/wallet.enc
with chmod 600. No API ever returns it, no log line ever contains it. Only the public key leaves this file.
Use a fresh wallet that holds only what you are willing to lose.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time

from . import db, fields, sources, vault

_kp = None
trade_lock = asyncio.Lock()   # one swap or balance attribution at a time


class WalletError(ValueError):
    pass


def _file():
    return db.DATA / "wallet.enc"


def parse_secret(text: str):
    from solders.keypair import Keypair
    import base58

    t = (text or "").strip()
    if not t:
        raise WalletError("paste the private key")
    words = t.split()
    if len(words) in (12, 15, 18, 21, 24) and all(w.isalpha() for w in words):
        raise WalletError("that is a recovery phrase. Export the private key of one account instead "
                          "(Phantom: Settings > Manage Accounts > the account > Show Private Key)")
    try:
        if t.startswith("["):
            raw = bytes(json.loads(t))
        else:
            raw = base58.b58decode(t)
    except Exception:
        raise WalletError("not a valid private key (expected base58 or a [64 numbers] array)")
    try:
        if len(raw) == 64:
            kp = Keypair.from_bytes(raw)
        elif len(raw) == 32:
            kp = Keypair.from_seed(raw)
        else:
            raise WalletError(f"private key has {len(raw)} bytes, expected 64")
    except WalletError:
        raise
    except Exception:
        raise WalletError("private key does not match its public half")
    return kp


def import_wallet(text: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    kp = parse_secret(text)
    key = base64.b64decode(vault.get("WALLET_KEY"))
    nonce = os.urandom(12)
    pub = str(kp.pubkey())
    ct = AESGCM(key).encrypt(nonce, bytes(kp), pub.encode())
    blob = {"pubkey": pub, "nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode(),
            "imported_at": time.time()}
    db.DATA.mkdir(parents=True, exist_ok=True)
    fd = os.open(_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(blob, fh)
    global _kp
    _kp = kp
    db.event("wallet_import", pub)
    return pub


def remove() -> None:
    global _kp
    _kp = None
    p = _file()
    if p.exists():
        p.write_bytes(os.urandom(p.stat().st_size))
        p.unlink()
    db.event("wallet_remove", "")


def meta() -> dict | None:
    p = _file()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        return {"pubkey": d["pubkey"], "imported_at": d.get("imported_at")}
    except Exception:
        return None


def pubkey() -> str | None:
    m = meta()
    return m["pubkey"] if m else None


def keypair():
    global _kp
    if _kp is not None:
        return _kp
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from solders.keypair import Keypair

    p = _file()
    if not p.exists():
        raise WalletError("no wallet imported")
    d = json.loads(p.read_text())
    key = base64.b64decode(vault.get("WALLET_KEY"))
    raw = AESGCM(key).decrypt(base64.b64decode(d["nonce"]), base64.b64decode(d["ct"]), d["pubkey"].encode())
    _kp = Keypair.from_bytes(raw)
    return _kp


async def sol_lamports(owner: str) -> int:
    res = await sources.rpc("getBalance", [owner])
    return int((res or {}).get("value", 0))


async def token_accounts(owner: str, mint: str) -> list[dict]:
    res = await sources.rpc("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
    out = []
    for acc in (res or {}).get("value", []):
        out.append({
            "pubkey": fields.get("rpc_token_account", "pubkey", acc),
            "amount": int(fields.get("rpc_token_account", "amount", acc) or 0),
            "decimals": int(fields.get("rpc_token_account", "decimals", acc) or 0),
            "program": fields.get("rpc_token_account", "program", acc),
        })
    return out


async def token_balance(owner: str, mint: str) -> tuple[int, int]:
    accs = await token_accounts(owner, mint)
    return sum(a["amount"] for a in accs), (accs[0]["decimals"] if accs else 0)


async def balances() -> dict:
    pub = pubkey()
    if not pub:
        return {"wallet": None}
    lamports = await sol_lamports(pub)
    usdc_raw, _ = await token_balance(pub, sources.USDC_MINT)
    px = await sources.sol_price()
    return {"wallet": pub, "lamports": lamports, "sol": lamports / 1e9, "sol_usd": lamports / 1e9 * px,
            "usdc": usdc_raw / 1e6, "sol_price": px, "ts": time.time()}
