"""Raw key mapping (STEP 2). Every agent reads source JSON only through this file.

Each path was read off a real response saved in desk/logs/raw/ on 2026-11-06:
  pumpportal_migration.json      PumpPortal subscribeMigration message
  dexscreener_pair.json          GET api.dexscreener.com/tokens/v1/solana/{mint}
  rugcheck_report.json           GET api.rugcheck.xyz/v1/tokens/{mint}/report
  rugcheck_insiders_graph.json   GET api.rugcheck.xyz/v1/tokens/{mint}/insiders/graph
  rpc_getAccountInfo.json        getAccountInfo(mint, jsonParsed)
  gpa_<program>.json             getProgramAccounts(Token / Token-2022, memcmp mint, jsonParsed)
  jupiter_quote.json / jupiter_swap.json   lite-api.jup.ag/swap/v1

None = the field is not in that source. We never guess a key.
"""
from __future__ import annotations

_MISSING = object()

MAP: dict[str, dict[str, str | None]] = {
    "pumpportal": {
        "mint": "mint",
        "signature": "signature",
        "tx_type": "txType",
        # "pool" in the message is the pool TYPE ("pump-amm"), not an address.
        "pool_type": "pool",
        "pool": None,       # -> DexScreener pairAddress
        "creator": None,    # -> RugCheck report "creator"
        "name": None,       # -> DexScreener baseToken.name
        "symbol": None,
    },
    "dexscreener": {
        "chain": "chainId",
        "dex": "dexId",
        "pair": "pairAddress",
        "mint": "baseToken.address",
        "name": "baseToken.name",
        "symbol": "baseToken.symbol",
        "quote_mint": "quoteToken.address",
        "price_usd": "priceUsd",
        "liquidity_usd": "liquidity.usd",
        "market_cap": "marketCap",
        "fdv": "fdv",
        "volume_m5": "volume.m5",
        "volume_h1": "volume.h1",
        "volume_h24": "volume.h24",
        "buys_m5": "txns.m5.buys",
        "sells_m5": "txns.m5.sells",
        "buys_h1": "txns.h1.buys",
        "sells_h1": "txns.h1.sells",
        "change_m5": "priceChange.m5",
        "change_h1": "priceChange.h1",
        "change_h24": "priceChange.h24",
        "pair_created_at": "pairCreatedAt",     # ms epoch
        "image": "info.imageUrl",
    },
    "rugcheck_report": {
        "creator": "creator",
        "creator_balance": "creatorBalance",
        "token_program": "tokenProgram",
        "mint_authority": "token.mintAuthority",
        "freeze_authority": "token.freezeAuthority",
        "supply": "token.supply",
        "decimals": "token.decimals",
        "markets": "markets",
        "market_pool": "pubkey",              # inside a markets[] entry
        "market_type": "marketType",          # inside a markets[] entry
        "market_lp_locked_pct": "lp.lpLockedPct",  # inside a markets[] entry
        "total_liquidity": "totalMarketLiquidity",
        "total_holders": "totalHolders",
        "top_holders": "topHolders",
        "known_accounts": "knownAccounts",
    },
    "rugcheck_graph": {
        # root is a list of networks; [] = RugCheck found no linked groups.
        "nodes": "nodes",
        "node_id": "id",
        "node_holdings": "holdings",
        "edges": "links",
        "edge_source": "source",
        "edge_target": "target",
    },
    "rpc_mint": {  # getAccountInfo(mint, jsonParsed) -> result
        "mint_authority": "value.data.parsed.info.mintAuthority",
        "freeze_authority": "value.data.parsed.info.freezeAuthority",
        "supply": "value.data.parsed.info.supply",
        "decimals": "value.data.parsed.info.decimals",
        "program_owner": "value.owner",
        "type": "value.data.parsed.type",
    },
    "rpc_token_account": {  # one item of getProgramAccounts / getTokenAccountsByOwner (jsonParsed)
        "pubkey": "pubkey",
        "owner": "account.data.parsed.info.owner",
        "mint": "account.data.parsed.info.mint",
        "amount": "account.data.parsed.info.tokenAmount.amount",
        "decimals": "account.data.parsed.info.tokenAmount.decimals",
        "delegate": "account.data.parsed.info.delegate",
        "program": "account.owner",
    },
    "rpc_supply": {  # getTokenSupply -> result
        "amount": "value.amount",
        "decimals": "value.decimals",
    },
    "rpc_largest": {  # getTokenLargestAccounts -> result.value[] item
        "address": "address",
        "amount": "amount",
    },
    "jupiter_quote": {
        "in_amount": "inAmount",
        "out_amount": "outAmount",
        "price_impact_pct": "priceImpactPct",
    },
    "jupiter_swap": {
        "tx": "swapTransaction",
        "last_valid_block_height": "lastValidBlockHeight",
        "priority_lamports": "prioritizationFeeLamports",
        "simulation_error": "simulationError",
    },
    "jupiter_price": {
        "usd": "usdPrice",   # inside the per-mint entry
    },
}


def _walk(obj, path: str):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def get(source: str, field: str, obj, default=None):
    path = MAP[source][field]
    if path is None or obj is None:
        return default
    v = _walk(obj, path)
    return default if v is _MISSING else v


def present(source: str, field: str, obj) -> bool:
    """True when the key exists in obj, even if its value is null (mintAuthority: null = revoked)."""
    path = MAP[source][field]
    if path is None or obj is None:
        return False
    return _walk(obj, path) is not _MISSING


def num(source: str, field: str, obj, default=None):
    v = get(source, field, obj)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def table() -> list[tuple[str, str, str | None]]:
    return [(src, f, path) for src, m in MAP.items() for f, path in m.items()]
