"""The morning Page (STEP 12): one HTML file per night, served at /report/{id} (unguessable id)."""
from __future__ import annotations

import html
import os
import secrets
import time
from collections import Counter

from . import book, config, db, links

CSS = """
:root{color-scheme:dark}body{margin:0;background:#000;color:#fff;font:15px/1.45 -apple-system,BlinkMacSystemFont,
"SF Pro Text",system-ui,sans-serif;font-variant-numeric:tabular-nums}main{max-width:720px;margin:0 auto;padding:24px 16px 48px}
h1{font:700 30px/1.15 -apple-system,"SF Pro Display",system-ui;margin:0 0 4px}h2{font-size:19px;margin:28px 0 8px}
.sub{color:rgba(235,235,245,.6)}.card{background:#1c1c1e;border-radius:14px;padding:4px 14px;margin:8px 0}
.row{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:.5px solid rgba(84,84,88,.55)}
.row:last-child{border-bottom:0}.r{text-align:right;white-space:nowrap}.g{color:#00d35a}.red{color:#ff453a}
a{color:#64d2ff;text-decoration:none}.alert{background:#3a0d0b;color:#ff453a;border-radius:14px;padding:12px 14px}
footer{margin-top:36px;color:rgba(235,235,245,.6)}
"""


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def coin_link(mint: str, label: str | None) -> str:
    return f'<a href="{esc(links.coin_url(mint, "solana"))}">{esc(label or mint[:6])}</a>'


def build(night_start: float, night_end: float | None = None) -> tuple[str, str]:
    end = night_end or time.time()
    mode = config.get("desk.mode")
    rng = (night_start, end)
    parts = [f"<h1>Night report</h1><div class=sub>{time.strftime('%a %d %b %Y', time.gmtime(end))} · {mode} · "
             f"{time.strftime('%H:%M', time.gmtime(night_start))}-{time.strftime('%H:%M', time.gmtime(end))} UTC</div>"]
    l8 = int(db.state_get(f"layer8_kills.{int(night_start)}", "0") or 0)
    if l8:
        parts.append(f'<h2>Layer 8 down</h2><div class=alert>{l8} coins turned red by "no data: delegations". '
                     f"Point rpc_url at an endpoint that allows getProgramAccounts.</div>")
    closed = db.query("SELECT * FROM positions WHERE status='closed' AND closed_at BETWEEN ? AND ? "
                      "ORDER BY pnl_usd ASC", rng)
    reds = [p for p in closed if (p["pnl_usd"] or 0) < 0]
    greens = [p for p in closed if (p["pnl_usd"] or 0) >= 0]
    total = sum(p["pnl_usd"] or 0 for p in closed)
    fees = sum(p["fees_usd"] or 0 for p in closed)
    parts.append(f"<h2>Trades · {len(closed)} closed · <span class={'g' if total >= 0 else 'red'}>"
                 f"{'+' if total >= 0 else '-'}${abs(total):.2f}</span></h2>")
    for title, rows in (("Red", reds), ("Green", greens)):
        if not rows:
            continue
        body = "".join(
            f"<div class=row><div>{coin_link(p['mint'], p['symbol'] or p['name'])}<div class=sub>"
            f"entry {p['entry_price']:.4g} → exit {p['exit_price']:.4g} · {esc(p['exit_reason'])}</div></div>"
            f"<div class=\"r {'g' if p['pnl_usd'] >= 0 else 'red'}\">{'+' if p['pnl_usd'] >= 0 else '-'}"
            f"${abs(p['pnl_usd']):.2f}<div class=sub>{p['pnl_usd'] / p['size_usd'] * 100:+.1f}%</div></div></div>"
            for p in (rows if title == "Red" else sorted(rows, key=lambda r: -r["pnl_usd"])))
        parts.append(f"<div class=sub>{title}</div><div class=card>{body}</div>")
    seen = db.scalar("SELECT COUNT(*) FROM coins WHERE first_seen BETWEEN ? AND ?", rng, 0)
    parts.append(f"<h2>Coins seen · {seen}</h2>")
    kills = db.query("SELECT reason FROM scans WHERE verdict IN ('kill','drop') AND ts BETWEEN ? AND ?", rng)
    by = Counter(((k["reason"] or "?").split(" ")[0] if not (k["reason"] or "").startswith("top 10") else "top 10")
                 for k in kills)
    if by:
        parts.append("<div class=sub>Scanner kills by reason</div><div class=card>" + "".join(
            f"<div class=row><div>{esc(r)}</div><div class=r>{n}</div></div>" for r, n in by.most_common()) + "</div>")
    refusals = db.query("SELECT a.mint, a.ts, a.layer, a.name, a.value_text, c.symbol, c.name AS cname FROM audits a "
                        "LEFT JOIN coins c ON c.mint=a.mint WHERE a.pass=0 AND a.ts BETWEEN ? AND ? "
                        "ORDER BY a.layer, a.ts", rng)
    if refusals:
        parts.append("<h2>Auditor refusals</h2><div class=card>" + "".join(
            f"<div class=row><div>{coin_link(r['mint'], r['symbol'] or r['cname'])}<div class=sub>layer {r['layer']} "
            f"{esc(r['name'])} · {esc(r['value_text'])}</div></div><div class=\"r sub\">"
            f"{time.strftime('%H:%M', time.gmtime(r['ts']))}</div></div>" for r in refusals) + "</div>")
    rejects = db.query("SELECT v.mint, v.reason, v.model, v.ts, c.symbol, c.name FROM verdicts v LEFT JOIN coins c "
                       "ON c.mint=v.mint WHERE v.pass=0 AND v.ts BETWEEN ? AND ? ORDER BY v.ts", rng)
    if rejects:
        parts.append("<h2>Analyst rejects</h2><div class=card>" + "".join(
            f"<div class=row><div>{coin_link(r['mint'], r['symbol'] or r['name'])}<div class=sub>"
            f"{'Analyst (rules)' if r['model'] == 'rules' else 'Analyst'} · {esc(r['reason'])}</div></div>"
            f"<div class=\"r sub\">{time.strftime('%H:%M', time.gmtime(r['ts']))}</div></div>" for r in rejects) + "</div>")
    tokens = db.scalar("SELECT SUM(input_tokens) FROM verdicts WHERE ts BETWEEN ? AND ?", rng, 0)
    price = os.environ.get("JEV_USD_PER_MTOK")
    cost = f"${tokens / 1e6 * float(price):.4f}" if price else "cost n/a (set JEV_USD_PER_MTOK)"
    parts.append(f"<h2>Costs</h2><div class=card><div class=row><div>Jev tokens</div><div class=r>{tokens:,} · {cost}"
                 f"</div></div><div class=row><div>Fees paid (SOL + swap)</div><div class=r>${fees:.2f}</div></div></div>")
    parts.append(f'<footer>New to FOMO? <a href="{esc(links.SIGNUP_URL)}">{esc(links.SIGNUP_URL)}</a></footer>')
    page = (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport "
            f"content=\"width=device-width,initial-scale=1\"><title>Night report</title><style>{CSS}</style></head>"
            f"<body><main>{''.join(parts)}</main></body></html>")
    rid = secrets.token_urlsafe(12)
    return rid, page


def save(night_start: float) -> str:
    rid, page = build(night_start)
    d = db.DATA / "reports"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{rid}.html"
    path.write_text(page, encoding="utf-8")
    db.execute("INSERT INTO reports(id, ts, mode, night_start, path) VALUES(?,?,?,?,?)",
               (rid, time.time(), book.mode(), night_start, str(path)))
    return rid
