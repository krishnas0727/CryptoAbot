"""
arbitrage_engine.py
===================
Core arbitrage opportunity calculator.

Formula:
    Gross Diff          = Highest Bid − Lowest Ask
    Gross Diff %        = (Gross Diff / Lowest Ask) × 100
    Units bought        = Trade Amount / Lowest Ask
    Gross Profit        = Units × Gross Diff
    Slippage Est.       = Trade Amount × 0.0005  (0.05%)
    Net Profit          = Gross Profit − Trading Fees − Withdrawal Fee − Slippage
    Net Profit %        = (Net Profit / Trade Amount) × 100

A trade is ONLY marked profitable when Net Profit > 0.
A large raw price difference does NOT automatically mean profit.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .price_scanner import TickerResult, scan_all_symbols, is_stale, SUPPORTED_SYMBOLS
from .fee_calculator import calculate_fees


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_TRADE_AMOUNT = 50.0          # USD / INR equivalent
SLIPPAGE_RATE        = 0.0005        # 0.05%
STATUS_PROFITABLE    = "profitable"
STATUS_LOW_MARGIN    = "low_margin"
STATUS_NOT_PROFITABLE= "not_profitable"
STATUS_INSUFFICIENT  = "insufficient_data"


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ArbitrageOpportunity:
    symbol:             str
    buy_exchange:       str
    sell_exchange:      str
    buy_price:          float          # lowest ask (buy from here)
    sell_price:         float          # highest bid (sell here)
    difference:         float          # sell_price − buy_price
    difference_percent: float          # gross % spread
    trade_amount:       float
    gross_profit:       float
    trading_fees:       float
    withdrawal_fee:     float
    slippage:           float
    net_profit:         float
    net_profit_percent: float
    status:             str
    currency:           str            # "USDT" or "INR"
    timestamp:          int = field(default_factory=lambda: int(time.time()))
    buy_volume:         Optional[float] = None
    sell_volume:        Optional[float] = None
    warning:            Optional[str]  = None


# ─────────────────────────────────────────────────────────────────────────────
# OPPORTUNITY FINDER
# ─────────────────────────────────────────────────────────────────────────────

def _has_valid_prices(ticker: TickerResult) -> bool:
    """Return True if ticker has at least a usable price (ask OR last)."""
    ask  = ticker.ask
    bid  = ticker.bid
    last = ticker.last
    return (
        ticker.status == "available"
        and any(p and p > 0 for p in [ask, bid, last])
    )


def _get_ask(t: TickerResult) -> Optional[float]:
    """Best available ask price: ask → last (treat as mid)."""
    if t.ask and t.ask > 0:
        return t.ask
    if t.last and t.last > 0:
        return t.last
    return None


def _get_bid(t: TickerResult) -> Optional[float]:
    """Best available bid price: bid → last."""
    if t.bid and t.bid > 0:
        return t.bid
    if t.last and t.last > 0:
        return t.last
    return None


def find_opportunities(
    scan_results: Dict[str, List[TickerResult]],
    trade_amount: float = DEFAULT_TRADE_AMOUNT,
) -> List[ArbitrageOpportunity]:
    """
    Compare all exchange pairs for each symbol.
    Returns a list of ArbitrageOpportunity, including non-profitable ones
    (so the UI can display the full picture).
    """
    opportunities: List[ArbitrageOpportunity] = []

    for symbol, tickers in scan_results.items():
        # Keep only tickers with valid prices
        valid = [t for t in tickers if _has_valid_prices(t) and not is_stale(t)]

        if len(valid) < 2:
            continue

        # Determine quote currency
        try:
            quote = symbol.split("/")[1].upper()
        except Exception:
            quote = "USDT"

        # Find the lowest ask (best to buy from)
        asks = [(t.exchange, _get_ask(t), t.volume) for t in valid]
        asks = [(ex, price, vol) for ex, price, vol in asks if price and price > 0]

        # Find the highest bid (best to sell to)
        bids = [(t.exchange, _get_bid(t), t.volume) for t in valid]
        bids = [(ex, price, vol) for ex, price, vol in bids if price and price > 0]

        if not asks or not bids:
            continue

        # Sort
        asks.sort(key=lambda x: x[1])   # ascending
        bids.sort(key=lambda x: x[1], reverse=True)  # descending

        # Consider top-3 ask × top-3 bid combos to find best net profit
        for buy_ex, ask_price, buy_vol in asks[:3]:
            for sell_ex, bid_price, sell_vol in bids[:3]:
                if buy_ex == sell_ex:
                    continue  # must be cross-exchange

                gross_diff = bid_price - ask_price

                # Even if spread is negative we record it (status = not_profitable)
                gross_diff_pct = (gross_diff / ask_price * 100) if ask_price > 0 else 0.0

                # Quantity of base asset purchased
                units = trade_amount / ask_price if ask_price > 0 else 0.0
                gross_profit = units * gross_diff

                # Fees
                sell_price_for_fee = bid_price
                fees_info = calculate_fees(
                    buy_exchange=buy_ex,
                    sell_exchange=sell_ex,
                    symbol=symbol,
                    trade_amount_usd=trade_amount,
                    sell_price_usd=sell_price_for_fee,
                )
                total_fees = fees_info["total_fees_usd"]

                # Slippage
                slippage = trade_amount * SLIPPAGE_RATE

                # Net profit
                net_profit = gross_profit - total_fees - slippage
                net_profit_pct = (net_profit / trade_amount * 100) if trade_amount > 0 else 0.0

                # Status
                if gross_diff <= 0 or not all([ask_price, bid_price]):
                    status = STATUS_INSUFFICIENT if not all([ask_price, bid_price]) else STATUS_NOT_PROFITABLE
                elif net_profit <= 0:
                    status = STATUS_NOT_PROFITABLE
                elif net_profit_pct < 0.3:
                    status = STATUS_LOW_MARGIN
                else:
                    status = STATUS_PROFITABLE

                # Warning flag
                warning = None
                if not tickers[0].bid or not tickers[0].ask:
                    warning = "Bid/ask data unavailable; using last price as estimate."
                if gross_diff > 0 and net_profit <= 0:
                    warning = "Raw spread exists but fees and slippage make this unprofitable."

                opp = ArbitrageOpportunity(
                    symbol=symbol,
                    buy_exchange=buy_ex,
                    sell_exchange=sell_ex,
                    buy_price=round(ask_price, 8),
                    sell_price=round(bid_price, 8),
                    difference=round(gross_diff, 8),
                    difference_percent=round(gross_diff_pct, 4),
                    trade_amount=trade_amount,
                    gross_profit=round(gross_profit, 4),
                    trading_fees=round(fees_info["trading_fees_usd"], 4),
                    withdrawal_fee=round(fees_info["withdrawal_fee_usd"], 4),
                    slippage=round(slippage, 4),
                    net_profit=round(net_profit, 4),
                    net_profit_percent=round(net_profit_pct, 4),
                    status=status,
                    currency=quote,
                    buy_volume=buy_vol,
                    sell_volume=sell_vol,
                    warning=warning,
                )
                opportunities.append(opp)

    return opportunities


# ─────────────────────────────────────────────────────────────────────────────
# FULL SCAN + FIND (convenience wrapper)
# ─────────────────────────────────────────────────────────────────────────────

def run_full_scan(
    symbols: Optional[List[str]] = None,
    trade_amount: float = DEFAULT_TRADE_AMOUNT,
    force: bool = False,
) -> List[ArbitrageOpportunity]:
    """
    Convenience function: scan all exchanges, find all opportunities.
    Returns list sorted by net_profit_percent descending.
    """
    scan_results = scan_all_symbols(symbols=symbols, force=force)
    opportunities = find_opportunities(scan_results, trade_amount=trade_amount)
    opportunities.sort(key=lambda o: o.net_profit_percent, reverse=True)
    return opportunities


def opportunity_to_dict(opp: ArbitrageOpportunity) -> dict:
    return {
        "symbol":             opp.symbol,
        "buy_exchange":       opp.buy_exchange,
        "sell_exchange":      opp.sell_exchange,
        "buy_price":          opp.buy_price,
        "sell_price":         opp.sell_price,
        "difference":         opp.difference,
        "difference_percent": opp.difference_percent,
        "trade_amount":       opp.trade_amount,
        "gross_profit":       opp.gross_profit,
        "trading_fees":       opp.trading_fees,
        "withdrawal_fee":     opp.withdrawal_fee,
        "slippage":           opp.slippage,
        "net_profit":         opp.net_profit,
        "net_profit_percent": opp.net_profit_percent,
        "status":             opp.status,
        "currency":           opp.currency,
        "timestamp":          opp.timestamp,
        "warning":            opp.warning,
    }
