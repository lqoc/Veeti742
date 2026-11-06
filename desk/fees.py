"""Fee model for PAPER PnL (STEP 11).

venue "fomo":   FOMO on Solana, per side: $5-$47.50 -> 2%; $47.50-$190 -> flat $0.95; above $190 -> 0.50%;
                then minus fees_ref_discount. Gas 0 (FOMO pays it).
venue "wallet": your own wallet through Jupiter: no swap fee from us or Jupiter's free API,
                only Solana network + priority fee, estimated per swap by wallet.paper_fee_usd.
LIVE wallet trades never use this file: they book the real SOL fee read from the transaction.
"""
from . import config


def fomo_side(ticket: float) -> float:
    if ticket <= 0:
        return 0.0
    if ticket < 47.50:
        fee = ticket * 0.02
    elif ticket <= 190:
        fee = 0.95
    else:
        fee = ticket * 0.005
    return round(fee * (1 - config.get("desk.fees_ref_discount")), 6)


def side(ticket: float) -> float:
    if config.get("desk.venue") == "fomo":
        return fomo_side(ticket)
    return round(config.get("wallet.paper_fee_usd"), 6)


def round_trip(entry_ticket: float, exit_ticket: float) -> float:
    return round(side(entry_ticket) + side(exit_ticket), 6)
