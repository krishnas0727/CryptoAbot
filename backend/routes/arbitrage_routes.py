"""
arbitrage_routes.py
===================
Flask Blueprint: /api/arbitrage/* and /api/chat and /api/exchanges/status

READ-ONLY.  No trades are executed from any of these endpoints.
"""

import time
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from backend.arbitrage.arbitrage_engine import (
    run_full_scan,
    opportunity_to_dict,
    DEFAULT_TRADE_AMOUNT,
    ArbitrageOpportunity,
)
from backend.arbitrage.opportunity_ranker import (
    rank, top_n, filter_by_base, filter_by_quote,
    filter_by_min_pct, dashboard_highlights, highest_net,
)
from backend.arbitrage.price_scanner import (
    scan_symbol, SUPPORTED_SYMBOLS,
)
from backend.arbitrage.exchange_manager import (
    check_all_exchanges, EXCHANGE_REGISTRY, STATUS_AVAILABLE,
)
from backend.arbitrage.chat_engine import (
    parse_and_respond, DEFAULT_TRADE_AMOUNT as CHAT_DEFAULT_AMOUNT,
)


arbitrage_bp = Blueprint("arbitrage_bp", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# SHARED IN-MEMORY OPPORTUNITY CACHE  (30s TTL)
# ─────────────────────────────────────────────────────────────────────────────

_opp_cache: dict = {"data": None, "ts": 0}
_OPP_TTL = 30  # seconds


def _get_opportunities(
    trade_amount: float = DEFAULT_TRADE_AMOUNT,
    force: bool = False,
) -> list:
    """Return cached or freshly-scanned opportunities."""
    now = time.time()
    if not force and _opp_cache["data"] and (now - _opp_cache["ts"]) < _OPP_TTL:
        return _opp_cache["data"]

    opps = run_full_scan(trade_amount=trade_amount, force=force)
    _opp_cache["data"] = opps
    _opp_cache["ts"]   = now
    return opps


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/arbitrage/opportunities
# ─────────────────────────────────────────────────────────────────────────────

@arbitrage_bp.route("/api/arbitrage/opportunities", methods=["GET"])
def get_all_opportunities():
    """
    Returns all arbitrage opportunities sorted by net profit %.
    Query params:
      - amount  (float)   trade amount for profit calc, default 50
      - min_pct (float)   minimum raw spread %, default 0
      - quote   (str)     filter by quote currency (USDT/INR)
      - force   (bool)    bypass cache
    """
    try:
        amount   = float(request.args.get("amount",  DEFAULT_TRADE_AMOUNT))
        min_pct  = float(request.args.get("min_pct", 0))
        quote    = request.args.get("quote", "").upper()
        force    = request.args.get("force", "false").lower() == "true"

        opps = _get_opportunities(trade_amount=amount, force=force)

        if min_pct > 0:
            opps = filter_by_min_pct(opps, min_pct)
        if quote:
            opps = filter_by_quote(opps, quote)

        opps = rank(opps)

        return jsonify({
            "success":        True,
            "count":          len(opps),
            "opportunities":  [opportunity_to_dict(o) for o in opps],
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "trade_amount":   amount,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/arbitrage/top
# ─────────────────────────────────────────────────────────────────────────────

@arbitrage_bp.route("/api/arbitrage/top", methods=["GET"])
def get_top_opportunities():
    """Top 10 opportunities by net profit %."""
    try:
        amount = float(request.args.get("amount", DEFAULT_TRADE_AMOUNT))
        n      = int(request.args.get("n", 10))
        force  = request.args.get("force", "false").lower() == "true"

        opps = _get_opportunities(trade_amount=amount, force=force)
        top  = top_n(opps, n)

        return jsonify({
            "success":       True,
            "count":         len(top),
            "opportunities": [opportunity_to_dict(o) for o in top],
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "trade_amount":  amount,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/arbitrage/compare/<symbol>
# ─────────────────────────────────────────────────────────────────────────────

@arbitrage_bp.route("/api/arbitrage/compare/<path:symbol>", methods=["GET"])
def compare_symbol(symbol: str):
    """
    All tickers + opportunities for a specific symbol.
    symbol example: BTC%2FUSDT  (URL encoded /)
    """
    try:
        symbol = symbol.replace("%2F", "/").upper()
        amount = float(request.args.get("amount", DEFAULT_TRADE_AMOUNT))
        force  = request.args.get("force", "false").lower() == "true"

        # Fetch raw tickers for the symbol
        tickers = scan_symbol(symbol, force=force)
        ticker_list = [
            {
                "exchange":  t.exchange,
                "symbol":    t.symbol,
                "bid":       t.bid,
                "ask":       t.ask,
                "last":      t.last,
                "volume":    t.volume,
                "timestamp": t.timestamp,
                "status":    t.status,
                "error":     t.error,
            }
            for t in tickers
        ]

        # Opportunities for this symbol
        opps = _get_opportunities(trade_amount=amount, force=force)
        sym_opps = [o for o in opps if o.symbol.upper() == symbol]

        return jsonify({
            "success":       True,
            "symbol":        symbol,
            "tickers":       ticker_list,
            "opportunities": [opportunity_to_dict(o) for o in sym_opps],
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "trade_amount":  amount,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/exchanges/status
# ─────────────────────────────────────────────────────────────────────────────

@arbitrage_bp.route("/api/exchanges/status", methods=["GET"])
def get_exchange_status():
    """Current availability status for all 11 exchanges."""
    try:
        force = request.args.get("force", "false").lower() == "true"
        statuses = check_all_exchanges(force=force)

        result = {}
        for name, st in statuses.items():
            info = EXCHANGE_REGISTRY.get(name)
            result[name] = {
                "status":     st.status,
                "latency_ms": st.latency_ms,
                "error":      st.error,
                "note":       st.note or (info.india_note if info else ""),
                "fee_taker":  info.fee_taker if info else None,
                "quote":      info.supported_quote if info else [],
            }

        available = sum(1 for s in statuses.values() if s.status == STATUS_AVAILABLE)

        return jsonify({
            "success":   True,
            "exchanges": result,
            "available": available,
            "total":     len(result),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/chat
# ─────────────────────────────────────────────────────────────────────────────

@arbitrage_bp.route("/api/chat", methods=["POST"])
def chat():
    """
    AI Arbitrage Chat Assistant.
    Body: { "message": "...", "trade_amount": 50 }
    Returns: { reply, opportunities, timestamp, data_source }
    """
    try:
        body         = request.get_json() or {}
        message      = (body.get("message") or "").strip()
        trade_amount = float(body.get("trade_amount") or CHAT_DEFAULT_AMOUNT)

        if not message:
            return jsonify({
                "success": False,
                "message": "No message provided.",
            }), 400

        # Fetch opportunities
        force = message.strip().lower() in ("/refresh", "refresh")
        opps  = _get_opportunities(trade_amount=trade_amount, force=force)

        # Fetch exchange statuses (from cache, non-blocking)
        statuses = check_all_exchanges(force=False)

        # Parse & respond
        response = parse_and_respond(
            message=message,
            opportunities=opps,
            exchange_statuses=statuses,
            trade_amount=trade_amount,
        )

        return jsonify({"success": True, **response})

    except Exception as e:
        return jsonify({
            "success": False,
            "reply":   f"⚠️ Error processing request: {str(e)[:200]}",
            "opportunities": [],
        }), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/market/<symbol>
# ─────────────────────────────────────────────────────────────────────────────

@arbitrage_bp.route("/api/market/<path:symbol>", methods=["GET"])
def get_market(symbol: str):
    """Market data for a specific symbol across all exchanges."""
    try:
        symbol  = symbol.replace("%2F", "/").upper()
        force   = request.args.get("force", "false").lower() == "true"
        tickers = scan_symbol(symbol, force=force)

        return jsonify({
            "success": True,
            "symbol":  symbol,
            "tickers": [
                {
                    "exchange":  t.exchange,
                    "symbol":    t.symbol,
                    "bid":       t.bid,
                    "ask":       t.ask,
                    "last":      t.last,
                    "volume":    t.volume,
                    "timestamp": t.timestamp,
                    "status":    t.status,
                    "error":     t.error,
                }
                for t in tickers
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/arbitrage/profit
# ─────────────────────────────────────────────────────────────────────────────

@arbitrage_bp.route("/api/arbitrage/profit", methods=["GET"])
def get_profit_estimate():
    """
    Profit estimate for a given trade amount.
    Query: amount (float), symbol (optional)
    """
    try:
        amount = float(request.args.get("amount", DEFAULT_TRADE_AMOUNT))
        symbol = request.args.get("symbol", "").upper()
        force  = request.args.get("force", "false").lower() == "true"

        opps = _get_opportunities(trade_amount=amount, force=force)

        if symbol:
            opps = [o for o in opps if o.symbol.upper() == symbol]

        best = highest_net(opps)
        if not best:
            return jsonify({
                "success": False,
                "message": "No opportunities available. Try refreshing.",
            }), 503

        # Rescale
        ratio = amount / best.trade_amount if best.trade_amount > 0 else 1
        return jsonify({
            "success":          True,
            "symbol":           best.symbol,
            "buy_exchange":     best.buy_exchange,
            "sell_exchange":    best.sell_exchange,
            "buy_price":        best.buy_price,
            "sell_price":       best.sell_price,
            "difference_pct":   best.difference_percent,
            "net_profit_pct":   best.net_profit_percent,
            "trade_amount":     amount,
            "gross_profit":     round(best.gross_profit * ratio, 4),
            "trading_fees":     round(best.trading_fees * ratio, 4),
            "slippage":         round(best.slippage * ratio, 4),
            "net_profit":       round(best.net_profit * ratio, 4),
            "status":           best.status,
            "currency":         best.currency,
            "timestamp":        datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/arbitrage/dashboard
# ─────────────────────────────────────────────────────────────────────────────

@arbitrage_bp.route("/api/arbitrage/dashboard", methods=["GET"])
def get_dashboard_highlights():
    """Dashboard widget data: top highlights per category."""
    try:
        amount = float(request.args.get("amount", DEFAULT_TRADE_AMOUNT))
        force  = request.args.get("force", "false").lower() == "true"

        opps = _get_opportunities(trade_amount=amount, force=force)
        hl   = dashboard_highlights(opps)

        def _d(o):
            return opportunity_to_dict(o) if o else None

        top5 = hl.get("top_5") or []

        return jsonify({
            "success":        True,
            "highest_spread": _d(hl.get("highest_spread")),
            "highest_net":    _d(hl.get("highest_net")),
            "most_liquid":    _d(hl.get("most_liquid")),
            "best_btc":       _d(hl.get("best_btc")),
            "best_eth":       _d(hl.get("best_eth")),
            "top_5":          [opportunity_to_dict(o) for o in top5],
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "trade_amount":   amount,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
