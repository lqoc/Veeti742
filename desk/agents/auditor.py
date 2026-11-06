"""AGENT 2 · AUDITOR. Takes every coin Scanner passes apart, 8 layers, in order. One red = dead."""
from __future__ import annotations

import asyncio
import time
import uuid

from .. import bus, config, db, fields, links, notifier, sources
from .base import Agent, iso, money, short

LAYERS = ["Mint authority", "Freeze authority", "Dev wallet", "Top 10 holders", "Liquidity", "Pool age",
          "Bundled wallets", "Delegations"]
NO_DATA = {3: "dev", 4: "top 10", 5: "liquidity", 6: "pool age", 7: "bundles", 8: "delegations", 1: "mint",
           2: "freeze"}


class NoData(Exception):
    pass


def biggest_group(graph) -> int | None:
    """Wallets that hold this mint, joined by funding/transfer links. None = graph unreadable."""
    if not isinstance(graph, list):
        return None
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    holds: dict[str, bool] = {}
    for net in graph:
        if not isinstance(net, dict) or not fields.present("rugcheck_graph", "edges", net) \
                or not fields.present("rugcheck_graph", "nodes", net):
            return None
        for n in fields.get("rugcheck_graph", "nodes", net) or []:
            nid = fields.get("rugcheck_graph", "node_id", n)
            if nid:
                find(nid)
                holds[nid] = holds.get(nid, False) or (fields.num("rugcheck_graph", "node_holdings", n, 0) or 0) > 0
        for e in fields.get("rugcheck_graph", "edges", net) or []:
            a, b = fields.get("rugcheck_graph", "edge_source", e), fields.get("rugcheck_graph", "edge_target", e)
            if a and b:
                parent[find(a)] = find(b)
    sizes: dict[str, int] = {}
    for nid, h in holds.items():
        if h:
            r = find(nid)
            sizes[r] = sizes.get(r, 0) + 1
    return max(sizes.values()) if sizes else 0


class Auditor(Agent):
    name = "auditor"

    def __init__(self) -> None:
        super().__init__()
        self.running: str | None = None
        self.layer8_down_alerted = False

    async def run(self) -> None:
        while True:
            self.beat()
            if self.paused:
                await asyncio.sleep(2)
                continue
            try:
                msg = await asyncio.wait_for(bus.q("auditor").get(), timeout=10)
            except asyncio.TimeoutError:
                continue
            try:
                await self.handle(msg)
            except Exception as e:
                self.log.info("%s audit crashed: %s", msg.get("mint"), e)

    async def handle(self, scan: dict) -> None:
        mint = scan["mint"]
        if db.one("SELECT 1 FROM audits WHERE mint=? AND ts>=? AND pass=0", (mint, self.night())):
            self.log.info("%s already rejected tonight", mint)
            return
        if not config.get("auditor.enabled"):
            out = {"agent": "auditor", "mint": mint, "ts": iso(), "passed": True, "layersChecked": 0,
                   "layers": [], "holders": None, "reason": None, "skipped": True}
            await bus.send("analyst", {"scan": scan, "audit": out})
            return
        out = await self.audit(scan)
        if out["passed"]:
            await bus.send("analyst", {"scan": scan, "audit": out})
        else:
            name = scan.get("name") or scan.get("symbol") or short(mint)
            db.execute("INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value) VALUES(?,?,?,?,?,?,?)",
                       (time.time(), "rejected", mint, "auditor", f"Rejected {name}", f"Auditor · {out['reason']}",
                        "Skipped"))
            bus.publish("activity", {"mint": mint})
            if config.get("desk.push_rejects"):
                await notifier.push(f"Rejected {name} · {out['reason']}", mint=mint)

    async def fetch(self, layer: int, fn):
        tries = config.get("auditor.retries")
        for attempt in range(tries + 1):
            try:
                return await fn()
            except sources.SourceError as e:
                self.log.info("layer %s source error (%s/%s): %s", layer, attempt + 1, tries + 1, e)
                if attempt < tries:
                    await asyncio.sleep(config.get("auditor.retry_gap_s"))
        raise NoData(NO_DATA[layer])

    async def audit(self, scan: dict, ad_hoc: bool = False) -> dict:
        mint = scan["mint"]
        coin = db.one("SELECT * FROM coins WHERE mint=?", (mint,)) or {}
        pool = scan.get("pool") or coin.get("pool")
        run_id = uuid.uuid4().hex[:12]
        self.running = mint
        cache: dict = {}

        async def once(key, layer, fn):
            if key not in cache:
                cache[key] = await self.fetch(layer, fn)
            return cache[key]

        layers = []
        reason = None
        holders = None
        bundled = None
        dev_pct = None
        lp_text = None
        deleg_text = None
        for n, lname in enumerate(LAYERS, start=1):
            bus.publish("audit_layer", {"mint": mint, "run": run_id, "n": n, "name": lname, "state": "running"})
            t0 = time.time()
            source = "rpc"
            ok, value = False, ""
            raw = None
            try:
                if n in (1, 2):
                    info = await once("mint", n, lambda: sources.mint_info(mint))
                    fld = "mint_authority" if n == 1 else "freeze_authority"
                    if not fields.present("rpc_mint", fld, info):
                        raise NoData(NO_DATA[n])
                    raw = fields.get("rpc_mint", fld, info)
                    ok = raw is None
                    value = "Revoked" if ok else ("mint not revoked" if n == 1 else "freeze not revoked")
                elif n == 3:
                    try:
                        supply, dec = await once("supply", 3, lambda: sources.token_supply(mint))
                    except NoData:
                        supply = 0
                    if supply <= 0:
                        ok, value = False, "no supply"
                    else:
                        cache["supply_n"] = supply
                        creator = coin.get("creator")
                        if not creator:
                            rep = await once("report", 3, lambda: sources.rug_report(mint))
                            creator = fields.get("rugcheck_report", "creator", rep)
                            source = "rugcheck"
                        at_mig = coin.get("creator_balance_at_migration")
                        if not creator or at_mig is None:
                            raise NoData("dev")
                        now_bal = await self.fetch(3, lambda: sources.owner_balance(creator, mint))
                        dev_pct = now_bal / supply * 100
                        raw = {"creator": creator, "now": now_bal, "at_migration": at_mig}
                        if dev_pct >= config.get("auditor.max_dev_pct"):
                            ok, value = False, f"dev holds {dev_pct:.0f}%"
                        elif now_bal < int(at_mig):
                            ok, value = False, "dev sold"
                        else:
                            ok, value = True, f"{dev_pct:.1f}% · sold 0"
                elif n == 4:
                    supply = cache.get("supply_n")
                    pct, how = await self.fetch(4, lambda: sources.top10_pct(mint, pool, supply))
                    raw = {"pct": pct, "how": how}
                    cache["top10"] = pct
                    ok = pct < config.get("auditor.max_top10_pct")
                    value = f"{pct:.1f}%" if ok else f"top 10 hold {pct:.0f}%"
                elif n == 5:
                    source = "rugcheck"
                    rep = await once("report", 5, lambda: sources.rug_report(mint))
                    markets = fields.get("rugcheck_report", "markets", rep) or []
                    m = next((x for x in markets if fields.get("rugcheck_report", "market_pool", x) == pool), None)
                    pct = fields.num("rugcheck_report", "market_lp_locked_pct", m) if m else None
                    if pct is None:
                        raise NoData("liquidity")
                    pairs = await self.fetch(5, lambda: sources.dex_tokens([mint]))
                    liq_now = sources.pair_view(pairs.get(mint)).get("liq")
                    liq_scan = scan.get("liquidityUsd") or coin.get("liq_at_scan")
                    raw = {"lp_locked_pct": pct, "market_type": fields.get("rugcheck_report", "market_type", m),
                           "liq_now": liq_now, "liq_scan": liq_scan}
                    burned = "burned" if fields.get("rugcheck_report", "market_type", m) == "pump_fun_amm" else "locked"
                    lp_text = f"{pct:.0f}% {burned}"
                    if pct < config.get("auditor.min_lp_locked_pct"):
                        ok, value = False, "liquidity unlocked"
                    elif liq_scan and (liq_now is None or liq_now < liq_scan * (1 - config.get("auditor.max_liq_drop_pct") / 100)):
                        ok, value = False, "liquidity pulled"
                    else:
                        ok, value = True, lp_text
                elif n == 6:
                    mig = coin.get("migrated_at") or time.time()
                    mins = (time.time() - mig) / 60
                    raw = mins
                    ok = mins >= config.get("auditor.min_pool_age_min")
                    value = (f"{int(mins)}m" if mins < 60 else f"{int(mins // 60)}h {int(mins % 60)}m") if ok \
                        else "pool too young"
                elif n == 7:
                    source = "rugcheck"
                    g = await once("graph", 7, lambda: sources.rug_graph(mint))
                    bundled = biggest_group(g)
                    raw = bundled
                    if bundled is None:
                        raise NoData("bundles")
                    ok = bundled < config.get("auditor.max_bundled")
                    value = ("None" if bundled <= 1 else f"{bundled} linked") if ok else f"{bundled} bundled wallets"
                elif n == 8:
                    try:
                        accs = await self.fetch(8, lambda: sources.token_accounts(mint))
                    except NoData:
                        await self.layer8_down()
                        raise
                    held = [a for a in accs if int(fields.get("rpc_token_account", "amount", a) or 0) > 0]
                    holders = len(held)
                    db.execute("UPDATE coins SET holders=? WHERE mint=?", (holders, mint))
                    by: dict[str, int] = {}
                    for a in held:
                        d = fields.get("rpc_token_account", "delegate", a)
                        if d:
                            by[d] = by.get(d, 0) + 1
                    top = max(by.items(), key=lambda kv: kv[1]) if by else (None, 0)
                    raw = {"accounts": len(accs), "holders": holders, "top_delegate": top}
                    ok = top[1] <= config.get("auditor.max_delegated_accounts")
                    deleg_text = "None" if not by else f"{top[1]} max"
                    value = deleg_text if ok else f"{top[1]} wallets → {short(top[0])}"
            except NoData as nd:
                ok, value = False, f"no data: {nd}"
            ms = int((time.time() - t0) * 1000)
            layers.append({"n": n, "name": lname, "pass": ok, "value": value, "source": source})
            db.execute("INSERT INTO audits(mint, ts, layer, name, value_text, pass, source, run_id, ms) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (mint, time.time(), n, lname, value, int(ok), source, run_id, ms))
            self.log.info("%s L%s %s raw=%s value=%r %sms", mint, n, "ok" if ok else "RED", str(raw)[:160], value, ms)
            bus.publish("audit_layer", {"mint": mint, "run": run_id, "n": n, "name": lname, "state": "done",
                                        "pass": ok, "value": value})
            if not ok:
                reason = value
                break
        for n in range(len(layers) + 1, 9):
            layers.append({"n": n, "name": LAYERS[n - 1], "pass": None, "value": "not checked", "source": None})
        passed = reason is None
        out = {"agent": "auditor", "mint": mint, "ts": iso(), "passed": passed,
               "layersChecked": sum(1 for x in layers if x["pass"] is not None), "layers": layers,
               "holders": holders, "reason": reason, "run": run_id,
               "extra": {"dev_pct": dev_pct, "top10": cache.get("top10"), "lp": lp_text, "bundled": bundled,
                         "delegations": deleg_text}}
        self.log.info("%s verdict %s %s", mint, "PASS 8/8" if passed else "REJECT", reason or "")
        bus.publish("audit", {"mint": mint, "passed": passed, "reason": reason, "run": run_id,
                              "name": scan.get("name"), "ad_hoc": ad_hoc})
        self.running = None
        return out

    async def layer8_down(self) -> None:
        key = f"layer8_kills.{int(self.night())}"
        db.state_set(key, int(db.state_get(key, "0") or 0) + 1)
        if not self.layer8_down_alerted:
            self.layer8_down_alerted = True
            await notifier.push("layer 8 down, every coin is red until rpc_url changes", trade=False)

    # ------------------------------------------------------------ app
    def card(self) -> dict:
        ns = self.night()
        runs = db.query("SELECT mint, run_id, MIN(pass) AS ok, COUNT(*) AS n FROM audits WHERE ts>=? "
                        "GROUP BY run_id ORDER BY MAX(id) DESC", (ns,))
        last = db.query("SELECT layer, pass, value_text FROM audits WHERE run_id=? ORDER BY layer",
                        (runs[0]["run_id"],)) if runs else []
        lastcoin = db.one("SELECT name, symbol FROM coins WHERE mint=?", (runs[0]["mint"],)) if runs else None
        rejected = sum(1 for r in runs if r["ok"] == 0)
        if self.running:
            status = "taking apart..."
        elif runs and runs[0]["ok"] == 1 and runs[0]["n"] == 8:
            status = "8/8 → Executor"
        elif last:
            status = next((x["value_text"] for x in last if not x["pass"]), "")
        else:
            status = "No coin taken apart yet"
        return {"name": "auditor", "title": "Auditor", "taken_apart": len(runs), "rejected": rejected,
                "status": status, "running": self.running,
                "strips": [{"n": x["layer"], "pass": bool(x["pass"])} for x in last],
                "coin": (lastcoin or {}).get("symbol") or (lastcoin or {}).get("name"),
                "mint": runs[0]["mint"] if runs else None}

    def decisions(self, limit: int = 20) -> list[dict]:
        runs = db.query("SELECT a.run_id, a.mint, MAX(a.ts) AS ts, MIN(a.pass) AS ok, COUNT(*) AS n, c.name, c.symbol "
                        "FROM audits a LEFT JOIN coins c ON c.mint=a.mint GROUP BY a.run_id "
                        "ORDER BY MAX(a.id) DESC LIMIT ?", (limit,))
        out = []
        for r in runs:
            red = db.one("SELECT value_text FROM audits WHERE run_id=? AND pass=0", (r["run_id"],))
            out.append({"ts": r["ts"], "mint": r["mint"], "name": r["name"], "symbol": r["symbol"],
                        "verdict": "pass" if r["ok"] == 1 and r["n"] == 8 else "reject",
                        "reason": red["value_text"] if red else "8/8 → analyst"})
        return out
