"""Swaps from your own wallet through Jupiter (lite-api.jup.ag/swap/v1). USDC in, USDC out.

buy(mint, usd)        USDC -> token
sell(mint, raw)       token -> USDC
close_empty(mint)     closes the empty token account after a full sell, rent comes back in SOL
Fills are read back from the confirmed transaction (meta pre/post balances), never assumed.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field

import httpx

from . import book, config, fields, sources, wallet

log = logging.getLogger("trader")
JUP = "https://lite-api.jup.ag/swap/v1"


@dataclass
class Trade:
    ok: bool
    reason: str | None = None
    sig: str | None = None
    usd: float = 0.0            # USDC spent (buy) or received (sell)
    tokens_raw: int = 0         # tokens received (buy) or sold (sell)
    decimals: int = 0
    price: float | None = None  # USD per token, from the real amounts
    sol_cost_usd: float = 0.0   # network + priority fee + rent paid (negative = rent refunded)
    impact_pct: float | None = None
    extra: dict = field(default_factory=dict)


async def quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int) -> dict:
    params = {"inputMint": input_mint, "outputMint": output_mint, "amount": str(amount),
              "slippageBps": str(slippage_bps), "restrictIntermediateTokens": "true"}
    r = await sources.client().get(f"{JUP}/quote", params=params)
    if r.status_code != 200:
        raise sources.SourceError(f"jupiter quote HTTP {r.status_code}: {r.text[:120]}")
    q = r.json()
    if not fields.get("jupiter_quote", "out_amount", q):
        raise sources.SourceError("jupiter: no route")
    return q


async def _build(q: dict, pub: str) -> dict:
    body = {"quoteResponse": q, "userPublicKey": pub, "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
                "maxLamports": config.get("wallet.max_priority_lamports"), "priorityLevel": "high"}}}
    r = await sources.client().post(f"{JUP}/swap", json=body)
    if r.status_code != 200:
        raise sources.SourceError(f"jupiter swap HTTP {r.status_code}: {r.text[:120]}")
    return r.json()


async def _send_and_confirm(raw_tx: bytes, last_valid: int | None, timeout_s: float = 75) -> tuple[str, str | None]:
    b64 = base64.b64encode(raw_tx).decode()
    sig = await sources.rpc("sendTransaction", [b64, {"encoding": "base64", "skipPreflight": True, "maxRetries": 2}])
    t0 = time.time()
    last_send = t0
    while time.time() - t0 < timeout_s:
        await asyncio.sleep(1.5)
        st = await sources.rpc("getSignatureStatuses", [[sig]], retries=1)
        v = (st or {}).get("value", [None])[0]
        if v:
            if v.get("err"):
                return sig, f"failed on chain: {v['err']}"
            if v.get("confirmationStatus") in ("confirmed", "finalized"):
                return sig, None
        if last_valid:
            try:
                h = await sources.rpc("getBlockHeight", [], retries=0)
                if h and h > last_valid:
                    return sig, "expired before it landed"
            except sources.SourceError:
                pass
        if time.time() - last_send > 4:
            last_send = time.time()
            try:
                await sources.rpc("sendTransaction", [b64, {"encoding": "base64", "skipPreflight": True,
                                                            "maxRetries": 0}], retries=0)
            except sources.SourceError:
                pass
    return sig, "not confirmed in time"


async def _deltas(sig: str, owner: str, mints: list[str]) -> dict:
    tx = None
    for _ in range(8):
        tx = await sources.rpc("getTransaction", [sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                                                         "maxSupportedTransactionVersion": 0}], retries=1)
        if tx:
            break
        await asyncio.sleep(1.5)
    if not tx:
        raise sources.SourceError("transaction not readable")
    meta = tx.get("meta") or {}

    def tok(side):
        out = {m: 0 for m in mints}
        dec = {}
        for b in meta.get(side) or []:
            if b.get("owner") == owner and b.get("mint") in out:
                out[b["mint"]] += int(b["uiTokenAmount"]["amount"])
                dec[b["mint"]] = int(b["uiTokenAmount"]["decimals"])
        return out, dec

    pre, _ = tok("preTokenBalances")
    post, dec = tok("postTokenBalances")
    lam = (meta.get("postBalances") or [0])[0] - (meta.get("preBalances") or [0])[0]
    return {"tokens": {m: post[m] - pre[m] for m in mints}, "decimals": dec, "lamports": lam,
            "fee": meta.get("fee", 0)}


async def _swap(input_mint: str, output_mint: str, amount: int, slippage: int, track: str) -> Trade:
    kp = wallet.keypair()
    pub = str(kp.pubkey())
    q = await quote(input_mint, output_mint, amount, slippage)
    impact = float(fields.get("jupiter_quote", "price_impact_pct", q) or 0) * 100
    if impact > config.get("wallet.max_price_impact_pct"):
        return Trade(False, f"price impact {impact:.1f}%", impact_pct=impact)
    built = await _build(q, pub)
    sim = fields.get("jupiter_swap", "simulation_error", built)
    if sim:
        return Trade(False, f"simulation: {sim.get('error') if isinstance(sim, dict) else sim}"[:120], impact_pct=impact)
    from solders.transaction import VersionedTransaction

    tx = VersionedTransaction.from_bytes(base64.b64decode(fields.get("jupiter_swap", "tx", built)))
    signed = VersionedTransaction(tx.message, [kp])
    sig, err = await _send_and_confirm(bytes(signed), fields.get("jupiter_swap", "last_valid_block_height", built))
    if err:
        return Trade(False, err, sig=sig, impact_pct=impact)
    d = await _deltas(sig, pub, [input_mint, output_mint, track])
    book.note_flow(d["tokens"].get(sources.USDC_MINT, 0), d["lamports"])
    px = await sources.sol_price()
    return Trade(True, None, sig=sig, impact_pct=impact,
                 sol_cost_usd=round(-d["lamports"] / 1e9 * px, 6),
                 extra={"deltas": d})


async def buy(mint: str, usd: float) -> Trade:
    amount = int(round(usd * 1e6))
    async with wallet.trade_lock:
        t = await _swap(sources.USDC_MINT, mint, amount, config.get("wallet.slippage_bps"), mint)
        if not t.ok:
            return t
        d = t.extra["deltas"]
        t.usd = -d["tokens"][sources.USDC_MINT] / 1e6
        t.tokens_raw = d["tokens"][mint]
        t.decimals = d["decimals"].get(mint, 0)
        if t.tokens_raw > 0:
            t.price = t.usd / (t.tokens_raw / 10 ** t.decimals)
        else:
            t.ok, t.reason = False, "no tokens received"
        return t


async def sell(mint: str, raw: int | None = None) -> Trade:
    async with wallet.trade_lock:
        pub = wallet.pubkey()
        have, dec = await wallet.token_balance(pub, mint)
        amount = have if raw is None else min(raw, have)
        if amount <= 0:
            return Trade(False, "nothing to sell")
        t = await _swap(mint, sources.USDC_MINT, amount, config.get("wallet.sell_slippage_bps"), mint)
        if not t.ok:
            return t
        d = t.extra["deltas"]
        t.usd = d["tokens"][sources.USDC_MINT] / 1e6
        t.tokens_raw = -d["tokens"][mint]
        t.decimals = dec
        if t.tokens_raw > 0:
            t.price = t.usd / (t.tokens_raw / 10 ** dec)
        return t


async def close_empty(mint: str) -> Trade:
    """Close every empty token account of this mint; the rent (~0.002 SOL each) returns to the wallet."""
    if not config.get("wallet.reclaim_rent"):
        return Trade(False, "off")
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.transaction import VersionedTransaction

    async with wallet.trade_lock:
        kp = wallet.keypair()
        owner = kp.pubkey()
        accs = [a for a in await wallet.token_accounts(str(owner), mint) if a["amount"] == 0]
        if not accs:
            return Trade(False, "no empty account")
        ixs = [Instruction(Pubkey.from_string(a["program"]), bytes([9]),
                           [AccountMeta(Pubkey.from_string(a["pubkey"]), False, True),
                            AccountMeta(owner, False, True), AccountMeta(owner, True, False)]) for a in accs]
        bh = await sources.rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
        v = bh["value"]
        msg = MessageV0.try_compile(owner, ixs, [], Hash.from_string(v["blockhash"]))
        tx = VersionedTransaction(msg, [kp])
        sig, err = await _send_and_confirm(bytes(tx), v.get("lastValidBlockHeight"), timeout_s=45)
        if err:
            return Trade(False, err, sig=sig)
        d = await _deltas(sig, str(owner), [mint])
        book.note_flow(0, d["lamports"])
        px = await sources.sol_price()
        return Trade(True, sig=sig, sol_cost_usd=round(-d["lamports"] / 1e9 * px, 6))
