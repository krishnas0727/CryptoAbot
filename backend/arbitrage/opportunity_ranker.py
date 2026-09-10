"""
opportunity_ranker.py
=====================
Sort, filter, and group arbitrage opportunities.
"""

from typing import Dict, List, Optional
from .arbitrage_engine import ArbitrageOpportunity, STATUS_PROFITABLE, STATUS_LOW_MARGIN


# ─────────────────────────────────────────────────────────────────────────────
# SORT
# ─────────────────────────────────────────────────────────────────────────────

def rank(opportunities: List[ArbitrageOpportunity]) -> List[ArbitrageOpportunity]:
    """Sort by net_profit_percent desc, then net_profit desc."""
    return sorted(
        opportunities,
        key=lambda o: (o.net_profit_percent, o.net_profit),
        reverse=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FILTERS
# ─────────────────────────────────────────────────────────────────────────────

def filter_profitable(opps: List[ArbitrageOpportunity]) -> List[ArbitrageOpportunity]:
    return [o for o in opps if o.status in (STATUS_PROFITABLE, STATUS_LOW_MARGIN)]


def filter_by_min_pct(
    opps: List[ArbitrageOpportunity], min_pct: float
) -> List[ArbitrageOpportunity]:
    return [o for o in opps if o.difference_percent >= min_pct]


def filter_by_min_net_pct(
    opps: List[ArbitrageOpportunity], min_pct: float
) -> List[ArbitrageOpportunity]:
    return [o for o in opps if o.net_profit_percent >= min_pct]


def filter_by_symbol(
    opps: List[ArbitrageOpportunity], symbol: str
) -> List[ArbitrageOpportunity]:
    sym_up = symbol.upper()
    return [o for o in opps if o.symbol.upper() == sym_up]


def filter_by_base(
    opps: List[ArbitrageOpportunity], base: str
) -> List[ArbitrageOpportunity]:
    """Filter by base coin, e.g. base='BTC' matches BTC/USDT and BTC/INR."""
    base_up = base.upper()
    return [o for o in opps if o.symbol.upper().startswith(f"{base_up}/")]


def filter_by_quote(
    opps: List[ArbitrageOpportunity], quote: str
) -> List[ArbitrageOpportunity]:
    """Filter by quote currency, e.g. quote='INR' or 'USDT'."""
    q_up = quote.upper()
    return [o for o in opps if o.symbol.upper().endswith(f"/{q_up}")]


# ─────────────────────────────────────────────────────────────────────────────
# TOP-N
# ─────────────────────────────────────────────────────────────────────────────

def top_n(opps: List[ArbitrageOpportunity], n: int = 10) -> List[ArbitrageOpportunity]:
    return rank(opps)[:n]


def highest_spread(opps: List[ArbitrageOpportunity]) -> Optional[ArbitrageOpportunity]:
    """Return the single opportunity with the highest gross spread %."""
    if not opps:
        return None
    return max(opps, key=lambda o: o.difference_percent)


def highest_net(opps: List[ArbitrageOpportunity]) -> Optional[ArbitrageOpportunity]:
    """Return the single opportunity with the highest net profit %."""
    if not opps:
        return None
    return max(opps, key=lambda o: o.net_profit_percent)


def best_per_coin(
    opps: List[ArbitrageOpportunity],
) -> Dict[str, ArbitrageOpportunity]:
    """For each base coin, return the best net-profit opportunity."""
    best: Dict[str, ArbitrageOpportunity] = {}
    for opp in opps:
        try:
            base = opp.symbol.split("/")[0]
        except Exception:
            continue
        if base not in best or opp.net_profit_percent > best[base].net_profit_percent:
            best[base] = opp
    return best


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD HIGHLIGHTS
# ─────────────────────────────────────────────────────────────────────────────

def dashboard_highlights(opps: List[ArbitrageOpportunity]) -> dict:
    """
    Return a dict of highlighted opportunities for the dashboard widget.
    """
    ranked = rank(opps)
    by_coin = best_per_coin(opps)

    return {
        "highest_spread":  highest_spread(opps),
        "highest_net":     highest_net(opps),
        "most_liquid":     _most_liquid(opps),
        "best_btc":        by_coin.get("BTC"),
        "best_eth":        by_coin.get("ETH"),
        "top_5":           ranked[:5],
    }


def _most_liquid(opps: List[ArbitrageOpportunity]) -> Optional[ArbitrageOpportunity]:
    """Return opportunity with highest reported buy_volume (if available)."""
    candidates = [o for o in opps if o.buy_volume and o.buy_volume > 0]
    if not candidates:
        return highest_net(opps)
    return max(candidates, key=lambda o: o.buy_volume)
