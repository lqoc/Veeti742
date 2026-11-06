"""Server-only secrets. Lives in DESK_DATA/secrets.json (chmod 600), never in the repo, web/, logs or API.

APP_TOKEN           app login, reaches the iPhone only through the URL fragment #t=
VAPID private key   web push signing key, stored as a PEM file next to secrets.json
WALLET_KEY          AES-256-GCM key that encrypts the imported wallet (wallet.enc)
TYPESAFE_API_KEY    optional Jev key (env var or set from the Account tab)
Environment variables of the same name override the file.
"""
from __future__ import annotations

import base64
import json
import os
import secrets as _secrets
from pathlib import Path

from . import db

_cache: dict | None = None


def _path() -> Path:
    db.DATA.mkdir(parents=True, exist_ok=True)
    return db.DATA / "secrets.json"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _write(d: dict) -> None:
    p = _path()
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(d, fh)
    os.replace(tmp, p)
    os.chmod(p, 0o600)


def _gen_vapid() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
    return pem, _b64u(pub)


def load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    p = _path()
    d = json.loads(p.read_text()) if p.exists() else {}
    changed = False
    if not d.get("APP_TOKEN"):
        d["APP_TOKEN"] = _secrets.token_urlsafe(32)
        changed = True
    if not d.get("WALLET_KEY"):
        d["WALLET_KEY"] = base64.b64encode(_secrets.token_bytes(32)).decode()
        changed = True
    if not d.get("VAPID_PUBLIC_KEY") or not (db.DATA / "vapid_private.pem").exists():
        pem, pub = _gen_vapid()
        fd = os.open(db.DATA / "vapid_private.pem", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(pem)
        d["VAPID_PUBLIC_KEY"] = pub
        changed = True
    if changed:
        _write(d)
    _cache = d
    return d


def get(name: str) -> str | None:
    env = os.environ.get(name)
    if env:
        return env
    return load().get(name)


def set_secret(name: str, value: str | None) -> None:
    d = dict(load())
    if value:
        d[name] = value
    else:
        d.pop(name, None)
    _write(d)
    global _cache
    _cache = d


def vapid_private_path() -> str:
    load()
    return str(db.DATA / "vapid_private.pem")


def secret_values() -> list[str]:
    """Every secret value, for the leak grep (STEP 13 #7)."""
    out = []
    for k in ("APP_TOKEN", "WALLET_KEY", "TYPESAFE_API_KEY"):
        v = get(k)
        if v:
            out.append(v)
    try:
        pem = Path(vapid_private_path()).read_text()
        body = "".join(l for l in pem.splitlines() if "-----" not in l)
        out.append(body[:40])
    except OSError:
        pass
    return out
