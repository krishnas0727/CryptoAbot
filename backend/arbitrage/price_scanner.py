"""
price_scanner.py
================
Concurrent multi-symbol price fetcher across all 11 exchanges.

For each (exchange, symbol) pair it returns:
    bid, ask, last, volume, timestamp, status, error

Results are cached for 15 seconds to avoid rate limiting.
One exchange failure never blocks others.
"""

import time
import json
import ssl
import urllib.request
import concurrent.futures
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import ccxt

from .exchange_manager import (
    EXCHANGE_REGISTRY,
    ExchangeInfo,
    _get_ccxt_instance,
    _ssl_ctx,
    STATUS_AVAILABLE,
    STATUS_API_UNAVAILABLE,
)


# ─────────────────────────────────────────────────────────────────────────────
# SUPPORTED SYMBOLS (normalised form)
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "BTC/INR",
    "ETH/INR",
    "USDT/INR",
]

# Exchange → which symbols it can provide
EXCHANGE_SYMBOLS: Dict[str, List[str]] = {
    "Bybit":   ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"],
    "Binance": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"],
    "KuCoin":  ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"],
    "OKX":     ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"],
    "Kraken":  ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"],
    "CoinDCX": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT",
                "BTC/INR",  "ETH/INR",  "USDT/INR"],
    "WazirX":  ["BTC/USDT", "ETH/USDT", "XRP/USDT",
                "BTC/INR",  "ETH/INR",  "USDT/INR"],
    "ZebPay":  ["BTC/INR",  "ETH/INR",  "USDT/INR"],
    "Bitbns":  ["BTC/INR",  "ETH/INR"],
    "Giottus": ["BTC/INR",  "ETH/INR"],
    "Mudrex":  ["BTC/USDT", "ETH/USDT"],
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TickerResult:
    exchange:  str
    symbol:    str
    bid:       Optional[float] = None
    ask:       Optional[float] = None
    last:      Optional[float] = None
    volume:    Optional[float] = None
    timestamp: Optional[int]   = None
    status:    str = STATUS_AVAILABLE
    error:     Optional[str]   = None


# ─────────────────────────────────────────────────────────────────────────────
# RESULT CACHE  (15-second TTL)
# ─────────────────────────────────────────────────────────────────────────────

_scan_cache: Dict[str, dict] = {}   # symbol → {ts, results}
_SCAN_TTL = 15


def _cached_scan(symbol: str) -> Optional[List[TickerResult]]:
    entry = _scan_cache.get(symbol)
    if entry and (time.time() - entry["ts"]) < _SCAN_TTL:
        return entry["results"]
    return None


def _cache_scan(symbol: str, results: List[TickerResult]):
    _scan_cache[symbol] = {"ts": time.time(), "results": results}


# ─────────────────────────────────────────────────────────────────────────────
# SYMBOL CONVERSION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _symbol_to_raw(symbol: str, exchange: str) -> str:
    """Convert 'BTC/USDT' → exchange-specific symbol string."""
    base, quote = symbol.split("/")
    if exchange == "Bitbns":
        return base           # Bitbns uses just the base coin name
    if exchange in ("ZebPay",):
        return f"{base}-{quote}"
    if exchange == "WazirX":
        return f"{base.lower()}{quote.lower()}"
    if exchange == "CoinDCX":
        return f"B-{base}_{quote}"
    if exchange == "Giottus":
        return f"{base}{quote}"   # BTCINR
    return f"{base}/{quote}"      # CCXT standard


# ─────────────────────────────────────────────────────────────────────────────
# PER-EXCHANGE TICKER FETCH (direct REST for Indian exchanges)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_ccxt(exchange_name: str, symbol: str) -> TickerResult:
    info: ExchangeInfo = EXCHANGE_REGISTRY[exchange_name]
    ex = _get_ccxt_instance(info.ccxt_id)
    if ex is None:
        return TickerResult(exchange=exchange_name, symbol=symbol,
                            status=STATUS_API_UNAVAILABLE, error="CCXT class not found")
    try:
        if exchange_name == "Binance":
            return _fetch_binance_direct(symbol)

        ticker = ex.fetch_ticker(symbol)
        return TickerResult(
            exchange=exchange_name,
            symbol=symbol,
            bid=float(ticker.get("bid") or 0) or None,
            ask=float(ticker.get("ask") or 0) or None,
            last=float(ticker.get("last") or 0) or None,
            volume=float(ticker.get("baseVolume") or 0) or None,
            timestamp=int(ticker.get("timestamp") or time.time() * 1000),
            status=STATUS_AVAILABLE,
        )
    except ccxt.BadSymbol:
        return TickerResult(exchange=exchange_name, symbol=symbol,
                            status=STATUS_API_UNAVAILABLE,
                            error=f"{symbol} not available on {exchange_name}")
    except Exception as e:
        err = str(e)[:120]
        st = "region_restricted" if "region" in err.lower() else STATUS_API_UNAVAILABLE
        return TickerResult(exchange=exchange_name, symbol=symbol,
                            status=st, error=err)


def _fetch_binance_direct(symbol: str) -> TickerResult:
    """Fetch from Binance Vision mirror (no auth, no region block)."""
    base, quote = symbol.split("/")
    raw = f"{base}{quote}"
    urls = [
        f"https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol={raw}",
        f"https://api.binance.us/api/v3/ticker/bookTicker?symbol={raw}",
    ]
    ctx = _ssl_ctx()
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=5).read())
            bid = float(data.get("bidPrice") or 0)
            ask = float(data.get("askPrice") or 0)
            if bid > 0 or ask > 0:
                last = (bid + ask) / 2 if bid and ask else (bid or ask)
                return TickerResult(
                    exchange="Binance",
                    symbol=symbol,
                    bid=bid or None,
                    ask=ask or None,
                    last=last or None,
                    timestamp=int(time.time() * 1000),
                    status=STATUS_AVAILABLE,
                )
        except Exception:
            continue
    # Fallback: price endpoint
    for url in [
        f"https://data-api.binance.vision/api/v3/ticker/price?symbol={raw}",
    ]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=5).read())
            price = float(data.get("price") or 0)
            if price > 0:
                return TickerResult(
                    exchange="Binance",
                    symbol=symbol,
                    last=price,
                    timestamp=int(time.time() * 1000),
                    status=STATUS_AVAILABLE,
                )
        except Exception:
            continue
    return TickerResult(exchange="Binance", symbol=symbol,
                        status=STATUS_API_UNAVAILABLE, error="All Binance mirrors failed")


def _fetch_coindcx(symbol: str) -> TickerResult:
    """CoinDCX public ticker API."""
    ctx = _ssl_ctx()
    try:
        req = urllib.request.Request(
            "https://api.coindcx.com/exchange/ticker",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=5).read())
        # CoinDCX returns a list of {market, bid, ask, last_price, volume, ...}
        base, quote = symbol.split("/")
        market_key = f"B-{base}_{quote}"
        for item in data:
            if item.get("market") == market_key:
                return TickerResult(
                    exchange="CoinDCX",
                    symbol=symbol,
                    bid=float(item.get("bid") or 0) or None,
                    ask=float(item.get("ask") or 0) or None,
                    last=float(item.get("last_price") or 0) or None,
                    volume=float(item.get("volume") or 0) or None,
                    timestamp=int(time.time() * 1000),
                    status=STATUS_AVAILABLE,
                )
        return TickerResult(exchange="CoinDCX", symbol=symbol,
                            status=STATUS_API_UNAVAILABLE,
                            error=f"{symbol} not found on CoinDCX")
    except Exception as e:
        return TickerResult(exchange="CoinDCX", symbol=symbol,
                            status=STATUS_API_UNAVAILABLE, error=str(e)[:120])


def _fetch_wazirx(symbol: str) -> TickerResult:
    ctx = _ssl_ctx()
    try:
        req = urllib.request.Request(
            "https://api.wazirx.com/sapi/v1/tickers/24hr",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=5).read())
        base, quote = symbol.split("/")
        raw = f"{base.lower()}{quote.lower()}"
        for item in data:
            if item.get("symbol") == raw:
                return TickerResult(
                    exchange="WazirX",
                    symbol=symbol,
                    bid=float(item.get("bidPrice") or 0) or None,
                    ask=float(item.get("askPrice") or 0) or None,
                    last=float(item.get("lastPrice") or 0) or None,
                    volume=float(item.get("volume") or 0) or None,
                    timestamp=int(time.time() * 1000),
                    status=STATUS_AVAILABLE,
                )
        return TickerResult(exchange="WazirX", symbol=symbol,
                            status=STATUS_API_UNAVAILABLE,
                            error=f"{symbol} not found on WazirX")
    except Exception as e:
        return TickerResult(exchange="WazirX", symbol=symbol,
                            status=STATUS_API_UNAVAILABLE, error=str(e)[:120])


def _fetch_zebpay(symbol: str) -> TickerResult:
    ctx = _ssl_ctx()
    try:
        base, quote = symbol.split("/")
        raw = f"{base}-{quote}"
        req = urllib.request.Request(
            f"https://api.zebapi.com/market/ticker?symbol={raw}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=5).read())
        # ZebPay returns {buy, sell, last, ...}
        bid  = float(data.get("buy") or data.get("bid") or 0) or None
        ask  = float(data.get("sell") or data.get("ask") or 0) or None
        last = float(data.get("last") or 0) or None
        if not any([bid, ask, last]):
            raise ValueError("Empty ticker")
        return TickerResult(
            exchange="ZebPay", symbol=symbol,
            bid=bid, ask=ask, last=last,
            timestamp=int(time.time() * 1000),
            status=STATUS_AVAILABLE,
        )
    except Exception as e:
        return TickerResult(exchange="ZebPay", symbol=symbol,
                            status=STATUS_API_UNAVAILABLE, error=str(e)[:120])


def _fetch_bitbns(symbol: str) -> TickerResult:
    ctx = _ssl_ctx()
    try:
        req = urllib.request.Request(
            "https://bitbns.com/order/getTickerWithVolume/",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=5).read())
        base, _ = symbol.split("/")
        coin_data = data.get(base.upper())
        if not coin_data:
            raise ValueError(f"{base} not in Bitbns response")
        highest_bid = float(coin_data.get("highest_buy_bid") or 0) or None
        lowest_ask  = float(coin_data.get("lowest_sell_ask") or 0) or None
        last        = float(coin_data.get("last_traded_price") or 0) or None
        return TickerResult(
            exchange="Bitbns", symbol=symbol,
            bid=highest_bid, ask=lowest_ask, last=last,
            timestamp=int(time.time() * 1000),
            status=STATUS_AVAILABLE,
        )
    except Exception as e:
        return TickerResult(exchange="Bitbns", symbol=symbol,
                            status=STATUS_API_UNAVAILABLE, error=str(e)[:120])


def _fetch_giottus(symbol: str) -> TickerResult:
    ctx = _ssl_ctx()
    try:
        req = urllib.request.Request(
            "https://www.giottus.com/api/v1/tickers",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=5).read())
        base, quote = symbol.split("/")
        raw = f"{base}{quote}"  # e.g. BTCINR
        tickers = data if isinstance(data, list) else data.get("data", [])
        for item in tickers:
            if item.get("name", "").upper() == raw.upper() or item.get("symbol", "").upper() == raw.upper():
                bid  = float(item.get("highest_bid") or item.get("bid") or 0) or None
                ask  = float(item.get("lowest_ask") or item.get("ask") or 0) or None
                last = float(item.get("last_price") or item.get("last") or 0) or None
                return TickerResult(
                    exchange="Giottus", symbol=symbol,
                    bid=bid, ask=ask, last=last,
                    timestamp=int(time.time() * 1000),
                    status=STATUS_AVAILABLE,
                )
        return TickerResult(exchange="Giottus", symbol=symbol,
                            status=STATUS_API_UNAVAILABLE,
                            error=f"{symbol} not found on Giottus")
    except Exception as e:
        return TickerResult(exchange="Giottus", symbol=symbol,
                            status=STATUS_API_UNAVAILABLE, error=str(e)[:120])


def _fetch_mudrex(symbol: str) -> TickerResult:
    """Mudrex public coin list — limited data."""
    ctx = _ssl_ctx()
    try:
        req = urllib.request.Request(
            "https://mudrex.com/api/v1/public/coins",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        raw = json.loads(urllib.request.urlopen(req, context=ctx, timeout=5).read())
        base, quote = symbol.split("/")
        data = raw if isinstance(raw, list) else raw.get("data", [])
        for item in data:
            if item.get("symbol", "").upper() == base.upper():
                price = float(item.get("price") or item.get("current_price") or 0)
                if price > 0:
                    return TickerResult(
                        exchange="Mudrex", symbol=symbol,
                        last=price,
                        timestamp=int(time.time() * 1000),
                        status=STATUS_AVAILABLE,
                    )
        return TickerResult(exchange="Mudrex", symbol=symbol,
                            status=STATUS_API_UNAVAILABLE,
                            error=f"{symbol} not found on Mudrex")
    except Exception as e:
        return TickerResult(exchange="Mudrex", symbol=symbol,
                            status=STATUS_API_UNAVAILABLE, error=str(e)[:120])


# ─────────────────────────────────────────────────────────────────────────────
# DISPATCH TABLE
# ─────────────────────────────────────────────────────────────────────────────

_DIRECT_FETCHERS = {
    "CoinDCX": _fetch_coindcx,
    "WazirX":  _fetch_wazirx,
    "ZebPay":  _fetch_zebpay,
    "Bitbns":  _fetch_bitbns,
    "Giottus": _fetch_giottus,
    "Mudrex":  _fetch_mudrex,
}


def fetch_ticker(exchange_name: str, symbol: str) -> TickerResult:
    """Fetch a single ticker result. Returns TickerResult with error fields on failure."""
    info = EXCHANGE_REGISTRY.get(exchange_name)
    if not info:
        return TickerResult(exchange=exchange_name, symbol=symbol,
                            status=STATUS_API_UNAVAILABLE, error="Unknown exchange")

    # Check this exchange actually claims to support this symbol
    supported = EXCHANGE_SYMBOLS.get(exchange_name, [])
    if symbol not in supported:
        return TickerResult(exchange=exchange_name, symbol=symbol,
                            status=STATUS_API_UNAVAILABLE,
                            error=f"{symbol} not available on {exchange_name}")

    if exchange_name in _DIRECT_FETCHERS:
        return _DIRECT_FETCHERS[exchange_name](symbol)

    if info.ccxt_id:
        return _fetch_ccxt(exchange_name, symbol)

    return TickerResult(exchange=exchange_name, symbol=symbol,
                        status=STATUS_API_UNAVAILABLE, error="No fetch method configured")


# ─────────────────────────────────────────────────────────────────────────────
# STALE PRICE VALIDATOR
# ─────────────────────────────────────────────────────────────────────────────

_STALE_THRESHOLD_MS = 120_000  # 2 minutes


def is_stale(result: TickerResult) -> bool:
    if result.timestamp is None:
        return False   # no timestamp → can't determine staleness
    age_ms = (time.time() * 1000) - result.timestamp
    return age_ms > _STALE_THRESHOLD_MS


# ─────────────────────────────────────────────────────────────────────────────
# SCAN ALL EXCHANGES FOR ONE SYMBOL  (concurrent)
# ─────────────────────────────────────────────────────────────────────────────

def scan_symbol(symbol: str, force: bool = False) -> List[TickerResult]:
    """
    Fetch ticker from all exchanges that claim to support this symbol.
    Results cached for 15s.
    """
    if not force:
        cached = _cached_scan(symbol)
        if cached:
            return cached

    exchanges_for_symbol = [
        name for name, syms in EXCHANGE_SYMBOLS.items() if symbol in syms
    ]

    results: List[TickerResult] = []

    def _safe_fetch(name: str) -> TickerResult:
        try:
            return fetch_ticker(name, symbol)
        except Exception as e:
            return TickerResult(exchange=name, symbol=symbol,
                                status=STATUS_API_UNAVAILABLE, error=str(e)[:120])

    max_workers = min(len(exchanges_for_symbol), 12)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_safe_fetch, name): name for name in exchanges_for_symbol}
        for future in concurrent.futures.as_completed(futures, timeout=12):
            try:
                results.append(future.result())
            except Exception as e:
                name = futures[future]
                results.append(TickerResult(exchange=name, symbol=symbol,
                                            status=STATUS_API_UNAVAILABLE,
                                            error=str(e)[:120]))

    _cache_scan(symbol, results)
    return results


def scan_all_symbols(symbols: Optional[List[str]] = None,
                     force: bool = False) -> Dict[str, List[TickerResult]]:
    """Scan multiple symbols concurrently. Returns {symbol: [TickerResult, ...]}"""
    if symbols is None:
        symbols = SUPPORTED_SYMBOLS

    results: Dict[str, List[TickerResult]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(symbols)) as executor:
        future_map = {executor.submit(scan_symbol, s, force): s for s in symbols}
        for future in concurrent.futures.as_completed(future_map, timeout=20):
            sym = future_map[future]
            try:
                results[sym] = future.result()
            except Exception as e:
                results[sym] = []

    return results
