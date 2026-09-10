"""
chat_engine.py
==============
NLP command parser and response formatter for the Arbitrage AI Assistant.

Handles:
  - Slash commands: /top /highest /10percent /btc /eth /inr /usdt
                    /profit N /exchanges /status /refresh
  - Natural language queries
  - Returns structured {reply, opportunities, timestamp, data_source}

The chat NEVER executes trades.  It is strictly read-only analysis.
"""

import time
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .arbitrage_engine import (
    ArbitrageOpportunity,
    opportunity_to_dict,
    STATUS_PROFITABLE,
    STATUS_LOW_MARGIN,
    STATUS_NOT_PROFITABLE,
    STATUS_INSUFFICIENT,
    DEFAULT_TRADE_AMOUNT,
)
from .opportunity_ranker import (
    rank, filter_profitable, filter_by_base, filter_by_quote,
    filter_by_min_pct, top_n, highest_spread, highest_net, best_per_coin,
)
from .exchange_manager import ExchangeStatus, STATUS_AVAILABLE


# ─────────────────────────────────────────────────────────────────────────────
# EMOJI / BADGE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _status_emoji(status: str) -> str:
    return {
        STATUS_PROFITABLE:   "🟢",
        STATUS_LOW_MARGIN:   "🟡",
        STATUS_NOT_PROFITABLE: "🔴",
        STATUS_INSUFFICIENT: "⚠️",
    }.get(status, "⚠️")


def _ex_emoji(ex_status: str) -> str:
    return {
        STATUS_AVAILABLE:          "🟢",
        "api_unavailable":         "🔴",
        "region_restricted":       "⛔",
        "trading_unavailable":     "🔴",
        "configuration_required":  "⚙️",
    }.get(ex_status, "⚠️")


def _currency_symbol(currency: str) -> str:
    return "₹" if currency == "INR" else "$"


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_price(price: float, currency: str) -> str:
    sym = _currency_symbol(currency)
    if price >= 1000:
        return f"{sym}{price:,.2f}"
    if price >= 1:
        return f"{sym}{price:.4f}"
    return f"{sym}{price:.6f}"


def _fmt_opp(opp: ArbitrageOpportunity, trade_amount: float = 50.0) -> str:
    """Format a single opportunity as a readable chat message."""
    cs = _currency_symbol(opp.currency)
    em = _status_emoji(opp.status)

    # Recalculate net profit for the given trade_amount if it differs
    if abs(trade_amount - opp.trade_amount) > 0.01:
        ratio = trade_amount / opp.trade_amount if opp.trade_amount > 0 else 1
        net = round(opp.net_profit * ratio, 4)
        fees = round(opp.trading_fees * ratio, 4)
        gross = round(opp.gross_profit * ratio, 4)
    else:
        net = opp.net_profit
        fees = opp.trading_fees
        gross = opp.gross_profit

    lines = [
        f"**{opp.symbol}**",
        f"Buy:  {opp.buy_exchange} @ {_fmt_price(opp.buy_price, opp.currency)}",
        f"Sell: {opp.sell_exchange} @ {_fmt_price(opp.sell_price, opp.currency)}",
        f"Spread: {opp.difference_percent:+.4f}%  |  Net: {opp.net_profit_percent:+.4f}%",
        f"Est. Net Profit ({cs}{trade_amount:.0f}): {cs}{net:.4f}",
        f"Fees: {cs}{fees:.4f}  |  Slippage: {cs}{opp.slippage:.4f}",
        f"Status: {em} {opp.status.replace('_', ' ').title()}",
    ]
    if opp.warning:
        lines.append(f"⚠️ Note: {opp.warning}")

    return "\n".join(lines)


DISCLAIMER = (
    "\n\n⚠️ **Disclaimer:** Prices are live estimates. "
    "Final profit depends on order fill quality, actual fees, "
    "network transfer costs, slippage, and market liquidity. "
    "Always verify before trading."
)


# ─────────────────────────────────────────────────────────────────────────────
# SLASH COMMAND HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _handle_top(opps: List[ArbitrageOpportunity], trade_amount: float) -> Tuple[str, list]:
    ranked = top_n(opps, 10)
    if not ranked:
        return "No arbitrage opportunities found across the scanned exchanges.", []
    lines = ["📊 **Top 10 Arbitrage Opportunities**\n"]
    for i, o in enumerate(ranked, 1):
        lines.append(f"**#{i}** {_status_emoji(o.status)} {o.symbol}")
        lines.append(f"  Buy {o.buy_exchange} → Sell {o.sell_exchange}")
        lines.append(
            f"  Spread: {o.difference_percent:+.4f}% | Net: {o.net_profit_percent:+.4f}% | "
            f"Est. Net ({_currency_symbol(o.currency)}{trade_amount:.0f}): "
            f"{_currency_symbol(o.currency)}{o.net_profit * (trade_amount / o.trade_amount):.4f}"
        )
        lines.append("")
    return "\n".join(lines) + DISCLAIMER, [opportunity_to_dict(o) for o in ranked]


def _handle_highest(opps: List[ArbitrageOpportunity], trade_amount: float) -> Tuple[str, list]:
    opp = highest_spread(opps)
    if not opp:
        return "No data available yet. Try /refresh to fetch latest prices.", []
    msg = (
        "🔥 **Highest Price Difference Found**\n\n"
        + _fmt_opp(opp, trade_amount)
        + DISCLAIMER
    )
    return msg, [opportunity_to_dict(opp)]


def _handle_10percent(opps: List[ArbitrageOpportunity], trade_amount: float) -> Tuple[str, list]:
    filtered = filter_by_min_pct(opps, 10.0)
    filtered = rank(filtered)
    if not filtered:
        return (
            "No opportunities with a raw spread ≥ 10% found.\n\n"
            "This is normal — crypto arbitrage spreads of 10%+ are extremely rare on "
            "liquid markets. Smaller profitable spreads may still exist. Try /top to see all.",
            [],
        )
    lines = [f"🔟 **Opportunities ≥ 10% Spread ({len(filtered)} found)**\n"]
    for o in filtered[:10]:
        lines.append(f"{_status_emoji(o.status)} **{o.symbol}**  {o.difference_percent:+.2f}%  →  Net {o.net_profit_percent:+.4f}%")
        lines.append(f"  Buy: {o.buy_exchange} @ {_fmt_price(o.buy_price, o.currency)}  |  Sell: {o.sell_exchange} @ {_fmt_price(o.sell_price, o.currency)}")
        if o.warning:
            lines.append(f"  ⚠️ {o.warning}")
        lines.append("")
    return "\n".join(lines) + DISCLAIMER, [opportunity_to_dict(o) for o in filtered[:10]]


def _handle_coin(coin: str, opps: List[ArbitrageOpportunity], trade_amount: float) -> Tuple[str, list]:
    filtered = filter_by_base(opps, coin)
    filtered = rank(filtered)
    if not filtered:
        return f"No data for {coin} across scanned exchanges. Exchanges may be unavailable.", []
    lines = [f"🪙 **{coin} Arbitrage Opportunities ({len(filtered)} found)**\n"]
    for o in filtered[:8]:
        lines.append(f"{_status_emoji(o.status)} {o.symbol}  Spread: {o.difference_percent:+.4f}%  Net: {o.net_profit_percent:+.4f}%")
        lines.append(f"  Buy {o.buy_exchange} @ {_fmt_price(o.buy_price, o.currency)}  →  Sell {o.sell_exchange} @ {_fmt_price(o.sell_price, o.currency)}")
        lines.append("")
    return "\n".join(lines) + DISCLAIMER, [opportunity_to_dict(o) for o in filtered[:8]]


def _handle_quote(quote: str, opps: List[ArbitrageOpportunity], trade_amount: float) -> Tuple[str, list]:
    filtered = filter_by_quote(opps, quote)
    filtered = rank(filtered)
    sym = "₹" if quote == "INR" else "$"
    if not filtered:
        return (
            f"No {quote} pair data available. "
            f"Indian exchanges (CoinDCX, WazirX, ZebPay) may be offline or not returning {quote} pairs.",
            [],
        )
    lines = [f"{'🇮🇳' if quote=='INR' else '💵'} **{quote} Pair Opportunities ({len(filtered)} found)**\n"]
    for o in filtered[:8]:
        lines.append(f"{_status_emoji(o.status)} {o.symbol}  Net: {o.net_profit_percent:+.4f}%")
        lines.append(f"  Buy {o.buy_exchange} {sym}{o.buy_price:,.2f}  →  Sell {o.sell_exchange} {sym}{o.sell_price:,.2f}")
        lines.append("")
    return "\n".join(lines) + DISCLAIMER, [opportunity_to_dict(o) for o in filtered[:8]]


def _handle_profit(amount: float, opps: List[ArbitrageOpportunity]) -> Tuple[str, list]:
    best = highest_net(opps)
    if not best:
        return "No data available. Try /refresh.", []

    # Rescale to the requested amount
    if best.trade_amount > 0:
        ratio = amount / best.trade_amount
    else:
        ratio = 1.0

    net    = round(best.net_profit * ratio, 4)
    fees   = round(best.trading_fees * ratio, 4)
    gross  = round(best.gross_profit * ratio, 4)
    slip   = round(best.slippage * ratio, 4)
    cs     = _currency_symbol(best.currency)

    msg = (
        f"💰 **Profit Estimate for {cs}{amount:.2f}**\n\n"
        f"Best opportunity: **{best.symbol}**\n"
        f"Buy: {best.buy_exchange} @ {_fmt_price(best.buy_price, best.currency)}\n"
        f"Sell: {best.sell_exchange} @ {_fmt_price(best.sell_price, best.currency)}\n\n"
        f"Gross Profit:   {cs}{gross:.4f}\n"
        f"Trading Fees:   − {cs}{fees:.4f}\n"
        f"Slippage Est.:  − {cs}{slip:.4f}\n"
        f"─────────────────────\n"
        f"Est. Net Profit: **{cs}{net:.4f}**  ({best.net_profit_percent:+.4f}%)\n"
        f"Status: {_status_emoji(best.status)} {best.status.replace('_', ' ').title()}"
        + DISCLAIMER
    )
    return msg, [opportunity_to_dict(best)]


def _handle_exchanges(exchange_statuses: Dict[str, ExchangeStatus]) -> Tuple[str, list]:
    lines = ["🌐 **Exchange Status — India Region**\n"]
    for name, st in exchange_statuses.items():
        em = _ex_emoji(st.status)
        lat = f"  {st.latency_ms:.0f}ms" if st.latency_ms else ""
        err = f"  ⚠️ {st.error[:60]}" if st.error else ""
        lines.append(f"{em} **{name}** — {st.status.replace('_', ' ').title()}{lat}{err}")
    return "\n".join(lines), []


def _handle_status(opps: List[ArbitrageOpportunity],
                   exchange_statuses: Dict[str, ExchangeStatus]) -> Tuple[str, list]:
    available = sum(1 for s in exchange_statuses.values() if s.status == STATUS_AVAILABLE)
    total     = len(exchange_statuses)
    profitable = [o for o in opps if o.status == STATUS_PROFITABLE]
    low        = [o for o in opps if o.status == STATUS_LOW_MARGIN]
    best = highest_net(opps)

    lines = [
        f"📡 **System Status**\n",
        f"Exchanges online: {available}/{total}",
        f"Profitable opportunities: {len(profitable)}",
        f"Low-margin opportunities: {len(low)}",
    ]
    if best:
        cs = _currency_symbol(best.currency)
        lines.append(
            f"\nBest opportunity: {best.symbol}  "
            f"Spread: {best.difference_percent:+.4f}%  Net: {best.net_profit_percent:+.4f}%"
        )
    lines.append(f"\nData timestamp: {datetime.now().strftime('%H:%M:%S')}")
    return "\n".join(lines), []


def _handle_buy_cheapest(coin: str, opps: List[ArbitrageOpportunity]) -> Tuple[str, list]:
    filtered = filter_by_base(opps, coin)
    if not filtered:
        return f"No data for {coin}.", []
    best_buy = min(filtered, key=lambda o: o.buy_price)
    cs = _currency_symbol(best_buy.currency)
    return (
        f"💸 **Cheapest place to buy {coin}**\n\n"
        f"Exchange: **{best_buy.buy_exchange}**\n"
        f"Price: {_fmt_price(best_buy.buy_price, best_buy.currency)}  ({best_buy.symbol})\n"
        + DISCLAIMER,
        [opportunity_to_dict(best_buy)],
    )


def _handle_sell_highest(coin: str, opps: List[ArbitrageOpportunity]) -> Tuple[str, list]:
    filtered = filter_by_base(opps, coin)
    if not filtered:
        return f"No data for {coin}.", []
    best_sell = max(filtered, key=lambda o: o.sell_price)
    cs = _currency_symbol(best_sell.currency)
    return (
        f"📈 **Highest place to sell {coin}**\n\n"
        f"Exchange: **{best_sell.sell_exchange}**\n"
        f"Price: {_fmt_price(best_sell.sell_price, best_sell.currency)}  ({best_sell.symbol})\n"
        + DISCLAIMER,
        [opportunity_to_dict(best_sell)],
    )


def _handle_why_not_profitable(opps: List[ArbitrageOpportunity]) -> Tuple[str, list]:
    non_prof = [o for o in opps if o.status == STATUS_NOT_PROFITABLE and o.difference > 0]
    if not non_prof:
        return (
            "All current opportunities with a positive raw spread are already "
            "being shown as profitable or low-margin. "
            "If you see a specific pair, try '/btc' or '/eth' to investigate.",
            [],
        )
    opp = max(non_prof, key=lambda o: o.difference_percent)
    cs = _currency_symbol(opp.currency)
    msg = (
        f"🔍 **Why is {opp.symbol} not profitable?**\n\n"
        f"Raw Spread: {opp.difference_percent:+.4f}%  ({_fmt_price(opp.difference, opp.currency)})\n"
        f"Gross Profit ({cs}{opp.trade_amount:.0f}): {cs}{opp.gross_profit:.4f}\n"
        f"Trading Fees: − {cs}{opp.trading_fees:.4f}  "
        f"(buy fee {opp.buy_exchange} + sell fee {opp.sell_exchange})\n"
        f"Withdrawal Fee: − {cs}{opp.withdrawal_fee:.4f}\n"
        f"Slippage: − {cs}{opp.slippage:.4f}  (0.05% conservative)\n"
        f"─────────────────────\n"
        f"Net Profit: **{cs}{opp.net_profit:.4f}**  → Not profitable after costs.\n\n"
        f"The raw price difference exists, but fees and slippage consume the spread."
    )
    return msg, [opportunity_to_dict(opp)]


# ─────────────────────────────────────────────────────────────────────────────
# NL INTENT DETECTION
# ─────────────────────────────────────────────────────────────────────────────

_COIN_PATTERN = re.compile(
    r"\b(BTC|ETH|SOL|XRP|DOGE|BNB|BTC/USDT|ETH/USDT|BTC/INR|ETH/INR)\b", re.IGNORECASE
)
_AMOUNT_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\b")


def _detect_coin(msg: str) -> Optional[str]:
    m = _COIN_PATTERN.search(msg)
    if m:
        coin = m.group(1).upper().split("/")[0]
        return coin
    return None


def _detect_amount(msg: str) -> float:
    m = _AMOUNT_PATTERN.search(msg)
    if m:
        return float(m.group(1))
    return DEFAULT_TRADE_AMOUNT


def _nl_parse(msg: str) -> str:
    """Map natural language to a canonical slash command."""
    low = msg.lower()

    # Compare / where to buy / cheapest
    if any(k in low for k in ["cheapest", "buy cheap", "where can i buy", "lowest buy", "buy from"]):
        coin = _detect_coin(msg) or "BTC"
        return f"/buycheap {coin}"
    if any(k in low for k in ["sell high", "where can i sell", "highest sell", "sell to"]):
        coin = _detect_coin(msg) or "BTC"
        return f"/sellhigh {coin}"

    if any(k in low for k in ["highest price diff", "highest spread", "largest diff", "highest arbitrage", "most profit", "best opport", "top opport"]):
        return "/highest"
    if any(k in low for k in ["show opport", "profitable opport", "show profitable"]):
        return "/top"
    if "10%" in low or "10 percent" in low or "above 10" in low or "over 10" in low:
        return "/10percent"
    if any(k in low for k in ["compare btc", "btc opport", "btc arb"]):
        return "/btc"
    if any(k in low for k in ["compare eth", "eth opport", "eth arb"]):
        return "/eth"
    if "inr" in low and ("show" in low or "only" in low or "pair" in low):
        return "/inr"
    if "usdt" in low and ("show" in low or "only" in low or "pair" in low):
        return "/usdt"
    if any(k in low for k in ["exchange status", "which exchange", "exchange avail"]):
        return "/exchanges"
    if "status" in low:
        return "/status"
    if any(k in low for k in ["refresh", "update", "latest", "reload"]):
        return "/refresh"
    if any(k in low for k in ["profit for", "profit if", "estimate profit", "profit with", "earn"]):
        amount = _detect_amount(msg)
        return f"/profit {amount}"
    if any(k in low for k in ["not profitable", "why not", "why is", "explain"]):
        return "/why"

    # Coin detection fallback
    coin = _detect_coin(msg)
    if coin:
        return f"/{coin.lower()}"

    # Default
    return "/top"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def parse_and_respond(
    message: str,
    opportunities: List[ArbitrageOpportunity],
    exchange_statuses: Dict[str, ExchangeStatus],
    trade_amount: float = DEFAULT_TRADE_AMOUNT,
) -> dict:
    """
    Parse a user message (slash command or natural language).
    Returns:
        {reply, opportunities, timestamp, data_source}
    """
    msg = message.strip()
    if not msg:
        return _build_response("Please type a question or command (e.g. /top or 'show highest spread').", [], trade_amount)

    # Normalise to slash command if NL
    if not msg.startswith("/"):
        msg = _nl_parse(msg)

    parts = msg.split()
    cmd   = parts[0].lower()
    args  = parts[1:] if len(parts) > 1 else []

    # ── SLASH COMMANDS ────────────────────────────────────────────────────────

    if cmd in ("/top", "/arbitrage"):
        reply, opps = _handle_top(opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    if cmd in ("/highest", "/best"):
        reply, opps = _handle_highest(opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/10percent":
        reply, opps = _handle_10percent(opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/btc":
        reply, opps = _handle_coin("BTC", opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/eth":
        reply, opps = _handle_coin("ETH", opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/sol":
        reply, opps = _handle_coin("SOL", opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/xrp":
        reply, opps = _handle_coin("XRP", opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/doge":
        reply, opps = _handle_coin("DOGE", opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/inr":
        reply, opps = _handle_quote("INR", opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/usdt":
        reply, opps = _handle_quote("USDT", opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/profit":
        amount = float(args[0]) if args else trade_amount
        reply, opps = _handle_profit(amount, opportunities)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/exchanges":
        reply, opps = _handle_exchanges(exchange_statuses)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/status":
        reply, opps = _handle_status(opportunities, exchange_statuses)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/refresh":
        return _build_response(
            "🔄 **Market data refresh triggered.**\n\nFetching latest prices from all exchanges. "
            "Results will update in a few seconds. Try /top after refresh.",
            [],
            trade_amount,
            data_source="refresh_triggered",
        )

    if cmd == "/buycheap":
        coin = args[0].upper() if args else "BTC"
        reply, opps = _handle_buy_cheapest(coin, opportunities)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/sellhigh":
        coin = args[0].upper() if args else "BTC"
        reply, opps = _handle_sell_highest(coin, opportunities)
        return _build_response(reply, opps, trade_amount)

    if cmd == "/why":
        reply, opps = _handle_why_not_profitable(opportunities)
        return _build_response(reply, opps, trade_amount)

    # Unknown command / coin shortcut
    coin_match = re.match(r"^/([a-z]+)$", cmd)
    if coin_match:
        coin = coin_match.group(1).upper()
        reply, opps = _handle_coin(coin, opportunities, trade_amount)
        return _build_response(reply, opps, trade_amount)

    return _build_response(
        f"Unknown command: `{cmd}`\n\n"
        "Available commands:\n"
        "/top  /highest  /10percent  /btc  /eth  /inr  /usdt  /profit N  /exchanges  /status  /refresh",
        [],
        trade_amount,
    )


def _build_response(
    reply: str,
    opps: list,
    trade_amount: float,
    data_source: str = "live",
) -> dict:
    return {
        "reply":        reply,
        "opportunities": opps,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "data_source":  data_source,
        "trade_amount": trade_amount,
    }
