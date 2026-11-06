"""ONE always-on process: 5 agents + API + routines in one asyncio loop.

    python -m desk.main                 run the desk (HOST/PORT env, default 0.0.0.0:8000)
    python -m desk.main --print-login   print the one-time app login link ({URL}#t=APP_TOKEN)
    python -m desk.main --import-wallet read a private key from the terminal (not echoed) and store it encrypted
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import logging.handlers
import os
import sys

from . import api, book, config, db, health, learn, publish, registry, tunnel, vault
from .agents.base import LOGS


def setup_logging() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    h = logging.handlers.TimedRotatingFileHandler(LOGS / "desk.log", when="midnight", backupCount=14, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root.addHandler(h)
    s = logging.StreamHandler(sys.stdout)
    s.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    s.setLevel(logging.INFO)
    root.addHandler(s)
    for noisy in ("httpx", "httpcore", "websockets", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def boot() -> None:
    db.connect()
    vault.load()
    config.load()
    registry.build()
    if book.run_start() == 0:
        book.start_run()
    book.night_start()
    preset = os.environ.get("DESK_PRESET", "").strip().lower()   # e.g. "learn", applied once
    if preset in config.PRESETS and db.state_get("preset_env") != preset and \
            (preset not in config.PAPER_ONLY or config.get("desk.mode") == "PAPER"):
        for line in config.apply_preset(preset, "env"):
            logging.getLogger("main").info("paper preset: %s", line)
        db.state_set("preset_env", preset)
    site = os.environ.get("DESK_SITE", "").strip().lower()   # deploy scripts turn the website on once
    if site in ("off", "deed") and db.state_get("site_env") != site:
        config.set_value("desk.site", site, "env")
        db.state_set("site_env", site)


async def heartbeat_main() -> None:
    while True:
        db.heartbeat("main")
        await asyncio.sleep(30)


async def run() -> None:
    import uvicorn

    boot()
    log = logging.getLogger("main")
    for name in registry.ORDER:
        health.start_agent(name)
    port = int(os.environ.get("PORT", "8000"))
    server = uvicorn.Server(uvicorn.Config(api.app, host=os.environ.get("HOST", "0.0.0.0"), port=port,
                                           log_level="warning", access_log=False, proxy_headers=True,
                                           forwarded_allow_ips="*"))
    log.info("desk up on :%s · mode %s · venue %s · analyst %s", port, config.get("desk.mode"),
             config.get("desk.venue"), config.get("analyst.mode"))
    w = config.ticket_check(book.stack_value())
    if w:
        log.info("ticket check: %s", w["text"])
    await asyncio.gather(server.serve(), book.sample_loop(), api.price_loop(), heartbeat_main(),
                         health.desk_health_loop(), health.night_report_loop(), tunnel.watch(),
                         learn.followup_loop(), publish.publisher.loop())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-login", action="store_true")
    ap.add_argument("--import-wallet", action="store_true")
    ap.add_argument("--url", default=os.environ.get("PUBLIC_URL", ""))
    args = ap.parse_args()
    if args.print_login:
        db.connect()
        url = args.url or db.state_get("public_url") or "https://YOUR-TUNNEL-URL"
        site = publish.login_link()
        if site:
            print(f"Website (read-only, stable): {site}")
        print(f"{url.rstrip('/')}/#t={vault.get('APP_TOKEN')}")
        return
    if args.import_wallet:
        from . import wallet

        db.connect()
        secret = getpass.getpass("Private key (base58 or [64 numbers], not shown): ")
        try:
            print("Imported", wallet.import_wallet(secret))
        except wallet.WalletError as e:
            print("Not imported:", e)
            sys.exit(1)
        return
    setup_logging()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
