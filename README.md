# Grok Bot desk

A private Solana memecoin desk: 5 agents in one always-on Python process, an iPhone web app, and
trading from **your own wallet**. No Telegram: pushes go to the app (bell + Web Push) and you talk to
the desk in the Account tab.

```
PumpPortal graduations -> Scanner -> Auditor (8 layers) -> Analyst (score) -> Executor (buys) -> Risk (sells)
```

- **Scanner** watches every pump.fun coin that graduates to PumpSwap and kills weak ones (liquidity, top-10 holders, 1h pump, copycats).
- **Auditor** takes each survivor apart: mint/freeze authority, dev wallet, top 10, LP burned, pool age, bundled wallets, delegations. One red = no.
- **Analyst** scores 0-100 (rules mode by default; Jev if you add a key).
- **Executor** sizes by score and buys USDC -> token through Jupiter, signed by your wallet:

  | score | 52-59 | 60-69 | 70-79 | 80-89 | 90+ |
  |---|---|---|---|---|---|
  | size | $0.50 | $1 | $2 | $3 | $4 |

  Max 3 open positions. Never spends the last $1 of USDC. Hard cap $5 per trade. Keeps 0.01 SOL for fees.
- **Risk** sells token -> USDC on stop -6%, trailing stop 12% (after +20%), or a 3h timeout, then closes the
  empty token account so the ~0.002 SOL rent comes back.

Every number is a setting in the Account tab (slider + exact field).

## Read this before LIVE

- This trades real money on very risky coins. Most memecoins go to zero. Use a **fresh wallet that only holds what you can lose**.
- The private key lives encrypted on the machine that runs the desk. Anyone who controls that machine can use it.
- LIVE unlocks only after 1 full PAPER night (closed by the morning report) and typing `LIVE`.
- The FOMO links in this app carry the referral code `savipww` from the original prompt. If that code isn't yours, it credits whoever wrote the prompt.

## Run it on your VM (Docker, with a Cloudflare tunnel)

The desk needs a machine that stays on (a $4-6/month VPS, a home server or a Raspberry Pi). The tunnel gives
it a public HTTPS URL without opening any port.

```bash
git clone https://github.com/lqoc/Veeti742.git && cd Veeti742
docker compose up -d --build
docker compose logs tunnel | grep trycloudflare          # your URL
docker compose exec desk python -m desk.main --print-login   # the one-time login link
```

Open the login link on your iPhone in Safari. The app saves the login, then the link is no longer needed.
The site is served live from the process (stream + 10 s refresh), so it updates as the agents work.

Quick tunnels are free but the URL changes when the tunnel restarts. For a fixed URL, create a tunnel in
Cloudflare Zero Trust > Networks > Tunnels, point it at `http://desk:8000`, and add to `.env`:
`TUNNEL_COMMAND=tunnel --no-autoupdate --metrics 0.0.0.0:2000 run --token <token>`.

### Without Docker (Ubuntu/Debian)

```bash
sudo bash deploy/install.sh          # venv + systemd + cloudflared quick tunnel
sudo bash deploy/install.sh login    # prints the login link
```

## On the iPhone

1. Open the login link in **Safari**.
2. Share > **Add to Home Screen** > Add. Open Grok Bot from the home screen.
3. Account > Notifications > **Enable notifications** > Allow. Then **Send test push**.

## Import your wallet

Phantom: Settings > Manage Accounts > pick the account > **Show Private Key** > copy. Never use the recovery phrase.

- In the app: Account > Your wallet > **Import wallet**, paste, Import. Or
- Over SSH (the key never crosses the internet): `docker compose exec desk python -m desk.main --import-wallet`

Fund the wallet with USDC (the trading money) and about 0.05 SOL (fees and token-account rent).

## Commands (Account > Talk to the desk)

`status` · `buy X` (audits X, places no order) · `sell X` (button to sell now) · `why X` · `pause Scanner` /
`resume Scanner` · `set stop to -8%` · `stack 1240` · `paper` / `LIVE` · `invite` · `report`

## Proofs captured while building (2026-11-06)

| Check | Result |
|---|---|
| Solana RPC getSlot | 465989236 |
| DexScreener /tokens/v1 | 200, PumpSwap pairs found |
| RugCheck report on graduated mint `GTTQ…pump` | 200, `markets[0].lp.lpLockedPct = 100` |
| **Layer 8**: getProgramAccounts Token + Token-2022, filter by mint | **passed** on the public RPC: Token 0, Token-2022 2,464 accounts (354 holders), 0.5 s |
| getTokenLargestAccounts on the public RPC | 429 (rate limited). Top 10 falls back to getProgramAccounts. A private RPC (Helius, QuickNode) is more reliable. |
| Jupiter quote + swap build + local signing | works (throwaway empty key, nothing sent) |
| Jev | skipped, Analyst runs in rules mode |
| Telegram | not used (your request). Web Push + in-app bell instead |

Raw responses are in `desk/logs/raw/`. Agents read them only through `desk/fields.py`:

| Field the agents need | Source | Exact key |
|---|---|---|
| mint, signature | PumpPortal migration | `mint`, `signature` |
| pool address | PumpPortal | **null**: `pool` is the pool type `"pump-amm"`. Taken from DexScreener `pairAddress` |
| creator | PumpPortal | **null**. Taken from RugCheck `creator` |
| name, symbol | PumpPortal | **null**. Taken from DexScreener `baseToken.name`, `baseToken.symbol` |
| price, liquidity, market cap | DexScreener | `priceUsd`, `liquidity.usd`, `marketCap` |
| volume, txns, change | DexScreener | `volume.m5/h1/h24`, `txns.m5/h1.buys/sells`, `priceChange.m5/h1/h24` |
| pool created | DexScreener | `pairCreatedAt` (ms) |
| mint / freeze authority | RPC getAccountInfo | `value.data.parsed.info.mintAuthority` / `.freezeAuthority` |
| supply | RPC getTokenSupply | `value.amount`, `value.decimals` |
| LP locked % (layer 5) | RugCheck report | `markets[pubkey == pool].lp.lpLockedPct` |
| bundles (layer 7) | RugCheck insiders graph | list of networks: `nodes[].id`, `nodes[].holdings`, `links[].source/target`; `[]` = none found |
| owner, amount, delegate (layers 4, 8) | RPC getProgramAccounts | `account.data.parsed.info.owner`, `.tokenAmount.amount`, `.delegate` |
| swap | Jupiter | `outAmount`, `priceImpactPct`, `swapTransaction`, `lastValidBlockHeight`, `simulationError` |

## Tests

```bash
pip install -r requirements.txt
python -m pytest desk/tests -q
```

`test_links` (every FOMO URL carries `r=savipww`, none built outside `links.py`, no shorteners), `test_gate`
(jev + rules thresholds), `test_risk` (trail, stop, timeout, stops never move down, `set stop to -8%`),
`test_executor` (FOMO ticket rules, the $0.50-$4 tiers, 3-position cap, $1 USDC reserve), `test_auditor`
(freeze authority red at layer 2), `test_replay` (one graduation through all 5 agents to a trailed exit),
`test_secrets` (no secret in repo, logs, API or the Page; 401 without the token).

## Files

```
desk/main.py       one asyncio process: agents + API + routines     desk/api.py      JSON + SSE, APP_TOKEN
desk/agents/       scanner auditor analyst executor risk              desk/trader.py   Jupiter swaps from your wallet
desk/wallet.py     encrypted key, balances                            desk/book.py     equity, top-ups, fees
desk/config.py     every setting, ranges, change log                  desk/fields.py   raw key mapping
desk/links.py      the only place FOMO URLs are built                 desk/report.py   morning Page
desk/health.py     desk-health (hourly) + night-report (daily)        desk/web/        the iPhone app
```
