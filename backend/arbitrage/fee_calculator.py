"""
fee_calculator.py
=================
Per-exchange fee lookup table and withdrawal fee estimates.
All fees are percentages (0.10 means 0.10%).

These are conservative public estimates; actual fees may vary with
VIP tier, token holdings, etc.
"""

from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# TRADING FEE TABLE  (taker fees — worst case)
# ─────────────────────────────────────────────────────────────────────────────

TRADING_FEES = {
    "Binance":  {"maker": 0.10, "taker": 0.10},
    "Bybit":    {"maker": 0.10, "taker": 0.10},
    "KuCoin":   {"maker": 0.10, "taker": 0.10},
    "OKX":      {"maker": 0.08, "taker": 0.10},
    "Kraken":   {"maker": 0.16, "taker": 0.26},
    "CoinDCX":  {"maker": 0.15, "taker": 0.25},
    "WazirX":   {"maker": 0.20, "taker": 0.20},
    "ZebPay":   {"maker": 0.15, "taker": 0.25},
    "Bitbns":   {"maker": 0.25, "taker": 0.25},
    "Giottus":  {"maker": 0.25, "taker": 0.25},
    "Mudrex":   {"maker": 0.00, "taker": 0.00},  # platform charges spread
}

# Default fallback
DEFAULT_TAKER_FEE = 0.25


# ─────────────────────────────────────────────────────────────────────────────
# WITHDRAWAL FEES  (flat amounts, not %)
# Coin → network → fee in coin units
# ─────────────────────────────────────────────────────────────────────────────

WITHDRAWAL_FEES = {
    "BTC": {
        "BTC":    0.0001,   # ~$6 at $60k
    },
    "ETH": {
        "ERC20":  0.001,    # ~$3 at $3k
    },
    "USDT": {
        "TRC20":  1.0,      # $1 flat
        "ERC20":  3.0,      # ~$3 flat
        "BEP20":  0.5,
    },
    "SOL": {
        "SOL":    0.01,
    },
    "XRP": {
        "XRP":    0.25,
    },
    "DOGE": {
        "DOGE":   1.0,
    },
}

# Best (cheapest) network per coin
BEST_WITHDRAWAL_FEE = {
    "BTC":  0.0001,
    "ETH":  0.001,
    "USDT": 1.0,
    "SOL":  0.01,
    "XRP":  0.25,
    "DOGE": 1.0,
    "BNB":  0.0005,
    "INR":  0.0,    # INR is domestic, no withdrawal fee modelled
}


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def get_taker_fee_pct(exchange_name: str) -> float:
    """Return taker fee as a percentage (e.g. 0.10 for 0.10%)."""
    entry = TRADING_FEES.get(exchange_name)
    if entry:
        return entry["taker"]
    return DEFAULT_TAKER_FEE


def get_maker_fee_pct(exchange_name: str) -> float:
    entry = TRADING_FEES.get(exchange_name)
    if entry:
        return entry["maker"]
    return DEFAULT_TAKER_FEE


def get_withdrawal_fee_in_usd(coin: str, sell_price_usd: float) -> float:
    """
    Return estimated withdrawal fee in USD for moving *coin* from
    the selling exchange after the arb trade.
    sell_price_usd is the current coin price in USD (or 0 for stablecoins).
    """
    coin_upper = coin.upper()
    fee_in_coin = BEST_WITHDRAWAL_FEE.get(coin_upper, 0.0)

    if coin_upper in ("USDT", "USDC", "BUSD", "DAI", "INR"):
        # Stablecoin: fee already in USD/INR units
        return fee_in_coin

    if sell_price_usd > 0:
        return fee_in_coin * sell_price_usd

    return 0.0


def calculate_fees(
    buy_exchange: str,
    sell_exchange: str,
    symbol: str,
    trade_amount_usd: float,
    sell_price_usd: float = 0.0,
) -> dict:
    """
    Calculate all estimated fees for an arbitrage round-trip.

    Returns:
        {
            buy_fee_usd, sell_fee_usd, trading_fees_usd,
            withdrawal_fee_usd, total_fees_usd
        }
    """
    try:
        base = symbol.split("/")[0].upper()
    except Exception:
        base = "BTC"

    buy_fee_pct  = get_taker_fee_pct(buy_exchange)
    sell_fee_pct = get_taker_fee_pct(sell_exchange)

    buy_fee_usd  = trade_amount_usd * (buy_fee_pct / 100)
    sell_fee_usd = trade_amount_usd * (sell_fee_pct / 100)   # approximate
    trading_fees = buy_fee_usd + sell_fee_usd

    withdrawal_fee = get_withdrawal_fee_in_usd(base, sell_price_usd)

    return {
        "buy_fee_usd":      round(buy_fee_usd, 4),
        "sell_fee_usd":     round(sell_fee_usd, 4),
        "trading_fees_usd": round(trading_fees, 4),
        "withdrawal_fee_usd": round(withdrawal_fee, 4),
        "total_fees_usd":   round(trading_fees + withdrawal_fee, 4),
    }
