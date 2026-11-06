"""HTTP clients for every outside source: DexScreener, RugCheck, Solana RPC, Jupiter price.

Agents never call httpx themselves; they come here and read the JSON through fields.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import httpx

from . import config, fields

log = logging.getLogger("sources")

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0),
                                    headers={"user-agent": "grok-desk/1.0"},
                                    limits=httpx.Limits(max_connections=20))
    return _client


class SourceError(Exception):
    pass


class RateLimited(SourceError):
    pass


# ------------------------------------------------------------------ DexScreener
_dex_calls: deque[float] = deque()
_dex_lock = asyncio.Lock()


async def _dex_slot() -> None:
    """Stay under scanner.dex_rpm requests per minute for the whole desk."""
    async with _dex_lock:
        while True:
            now = time.time()
            while _dex_calls and now - _dex_calls[0] > 60:
                _dex_calls.popleft()
            if len(_dex_calls) < config.get("scanner.dex_rpm"):
                _dex_calls.append(now)
                return
            await asyncio.sleep(60 - (now - _dex_calls[0]) + 0.05)


def dex_calls_last_min() -> int:
    now = time.time()
    return sum(1 for t in _dex_calls if now - t <= 60)


def best_pair(pairs: list[dict], mint: str) -> dict | None:
    mine = [p for p in pairs if fields.get("dexscreener", "mint", p) == mint]
    pump = [p for p in mine if fields.get("dexscreener", "dex", p) == "pumpswap"]
    pool = pump or mine
    if not pool:
        return None
    return max(pool, key=lambda p: fields.num("dexscreener", "liquidity_usd", p, 0) or 0)


async def dex_tokens(mints: list[str]) -> dict[str, dict]:
    """mint -> best pair (PumpSwap first, most liquidity). One request per 30 mints."""
    out: dict[str, dict] = {}
    mints = list(dict.fromkeys(m for m in mints if m))
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        await _dex_slot()
        try:
            r = await client().get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(chunk))
        except httpx.HTTPError as e:
            raise SourceError(f"dexscreener: {e.__class__.__name__}") from e
        if r.status_code == 429:
            raise RateLimited("dexscreener 429")
        if r.status_code != 200:
            raise SourceError(f"dexscreener HTTP {r.status_code}")
        pairs = r.json() or []
        for m in chunk:
            p = best_pair(pairs, m)
            if p:
                out[m] = p
    return out


def pair_view(p: dict | None) -> dict:
    """Flatten a DexScreener pair into the numbers the agents and the app use."""
    g = lambda k: fields.num("dexscreener", k, p)  # noqa: E731
    return {
        "pair": fields.get("dexscreener", "pair", p),
        "dex": fields.get("dexscreener", "dex", p),
        "name": fields.get("dexscreener", "name", p),
        "symbol": fields.get("dexscreener", "symbol", p),
        "price": g("price_usd"), "liq": g("liquidity_usd"), "mcap": g("market_cap"),
        "v5": g("volume_m5"), "v1": g("volume_h1"), "v24": g("volume_h24"),
        "b5": g("buys_m5"), "s5": g("sells_m5"), "b1": g("buys_h1"), "s1": g("sells_h1"),
        "p5": g("change_m5"), "p1": g("change_h1"), "p24": g("change_h24"),
        "created": g("pair_created_at"), "image": fields.get("dexscreener", "image", p),
    }


# ------------------------------------------------------------------ RugCheck
async def _rug(path: str):
    try:
        r = await client().get(f"https://api.rugcheck.xyz/v1/tokens/{path}")
    except httpx.HTTPError as e:
        raise SourceError(f"rugcheck: {e.__class__.__name__}") from e
    if r.status_code == 429:
        raise RateLimited("rugcheck 429")
    if r.status_code != 200:
        raise SourceError(f"rugcheck HTTP {r.status_code}")
    return r.json()


async def rug_report(mint: str) -> dict:
    return await _rug(f"{mint}/report")


async def rug_graph(mint: str):
    return await _rug(f"{mint}/insiders/graph")


# ------------------------------------------------------------------ Solana RPC
_rpc_id = 0


async def rpc(method: str, params: list, retries: int = 3, timeout: float = 30.0):
    global _rpc_id
    url = config.get("desk.rpc_url")
    last = None
    for attempt in range(retries + 1):
        _rpc_id += 1
        try:
            r = await client().post(url, json={"jsonrpc": "2.0", "id": _rpc_id, "method": method, "params": params},
                                    timeout=timeout)
        except httpx.HTTPError as e:
            last = SourceError(f"rpc {method}: {e.__class__.__name__}")
        else:
            if r.status_code == 429:
                last = RateLimited(f"rpc {method} 429")
            elif r.status_code != 200:
                last = SourceError(f"rpc {method} HTTP {r.status_code}")
            else:
                body = r.json()
                if "error" in body:
                    err = body["error"]
                    if isinstance(err, dict) and err.get("code") == 429:
                        last = RateLimited(f"rpc {method} 429")
                    else:
                        raise SourceError(f"rpc {method}: {err.get('message') if isinstance(err, dict) else err}")
                else:
                    return body.get("result")
        if attempt < retries:
            await asyncio.sleep(1.5 * (attempt + 1))
    raise last or SourceError(f"rpc {method} failed")


async def mint_info(mint: str) -> dict:
    res = await rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    if not res or not res.get("value"):
        raise SourceError("mint account not found")
    return res


async def token_supply(mint: str) -> tuple[int, int]:
    res = await rpc("getTokenSupply", [mint])
    amt = fields.get("rpc_supply", "amount", res)
    dec = fields.get("rpc_supply", "decimals", res)
    if amt is None:
        raise SourceError("no supply")
    return int(amt), int(dec or 0)


async def token_accounts(mint: str) -> list[dict]:
    """Every token account of the mint, Token AND Token-2022 (layer 8, holders, top-10 fallback)."""
    out = []
    for prog in (TOKEN_PROGRAM, TOKEN_2022):
        res = await rpc("getProgramAccounts",
                        [prog, {"encoding": "jsonParsed", "filters": [{"memcmp": {"offset": 0, "bytes": mint}}]}],
                        retries=2, timeout=45.0)
        if res is None:
            raise SourceError("getProgramAccounts returned nothing")
        out.extend(res)
    return out


async def owner_balance(owner: str, mint: str) -> int:
    res = await rpc("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
    total = 0
    for acc in (res or {}).get("value", []):
        total += int(fields.get("rpc_token_account", "amount", acc) or 0)
    return total


def curve_pda(mint: str) -> str:
    from solders.pubkey import Pubkey
    pda, _ = Pubkey.find_program_address([b"bonding-curve", bytes(Pubkey.from_string(mint))],
                                         Pubkey.from_string(PUMP_PROGRAM))
    return str(pda)


async def top10_pct(mint: str, pool: str | None, supply: int) -> tuple[float, str]:
    """Top 10 holders % of supply, pool and bonding curve excluded. Returns (pct, how)."""
    excluded = {x for x in (pool, curve_pda(mint)) if x}
    if supply <= 0:
        raise SourceError("no supply")
    try:
        res = await rpc("getTokenLargestAccounts", [mint], retries=1)
        addrs = [fields.get("rpc_largest", "address", a) for a in (res or {}).get("value", [])]
        amounts = {fields.get("rpc_largest", "address", a): int(fields.get("rpc_largest", "amount", a) or 0)
                   for a in (res or {}).get("value", [])}
        info = await rpc("getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}])
        holders = []
        for addr, acc in zip(addrs, (info or {}).get("value", [])):
            owner = fields.get("rpc_token_account", "owner", {"account": acc}) if acc else None
            if owner in excluded or addr in excluded:
                continue
            holders.append(amounts.get(addr, 0))
        how = "largest"
    except SourceError:
        accs = await token_accounts(mint)
        holders = [int(fields.get("rpc_token_account", "amount", a) or 0) for a in accs
                   if fields.get("rpc_token_account", "owner", a) not in excluded
                   and fields.get("rpc_token_account", "pubkey", a) not in excluded]
        how = "all accounts"
    holders.sort(reverse=True)
    return round(sum(holders[:10]) / supply * 100, 2), how


# ------------------------------------------------------------------ Jupiter price
_sol_px: tuple[float, float] = (0.0, 0.0)


async def sol_price() -> float:
    global _sol_px
    if time.time() - _sol_px[1] < 30 and _sol_px[0] > 0:
        return _sol_px[0]
    try:
        r = await client().get(f"https://lite-api.jup.ag/price/v3?ids={SOL_MINT}")
        v = fields.num("jupiter_price", "usd", (r.json() or {}).get(SOL_MINT))
        if v:
            _sol_px = (v, time.time())
    except Exception as e:  # keep the last good price
        log.warning("sol price: %s", e)
    return _sol_px[0]
