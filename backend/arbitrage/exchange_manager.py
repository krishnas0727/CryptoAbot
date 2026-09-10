"""
exchange_manager.py
===================
Registry and availability checker for 11 exchanges accessible
to Indian users.  Each exchange is fetched independently with a
per-exchange timeout; one failure never stops the others.

Reusable CCXT instances are cached at module load — no new instance
is created per request.
"""

import time
import ssl
import json
import urllib.request
import concurrent.futures
from dataclasses import dataclass, field
from typing import Dict, Optional

import ccxt


# ─────────────────────────────────────────────────────────────────────────────
# STATUS CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

STATUS_AVAILABLE            = "available"
STATUS_API_UNAVAILABLE      = "api_unavailable"
STATUS_REGION_RESTRICTED    = "region_restricted"
STATUS_TRADING_UNAVAILABLE  = "trading_unavailable"
STATUS_CONFIG_REQUIRED      = "configuration_required"


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExchangeInfo:
    name: str
    ccxt_id: Optional[str]          # ccxt class name, e.g. "bybit"
    direct_url: Optional[str]       # fallback direct REST URL template
    fee_maker: float                 # maker fee %
    fee_taker: float                 # taker fee %
    india_note: str                  # human note for Indian users
    supported_quote: list            # ["USDT", "INR", ...]


@dataclass
class ExchangeStatus:
    name: str
    status: str
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    note: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# EXCHANGE REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

EXCHANGE_REGISTRY: Dict[str, ExchangeInfo] = {

    "Bybit": ExchangeInfo(
        name="Bybit",
        ccxt_id="bybit",
        direct_url=None,
        fee_maker=0.10,
        fee_taker=0.10,
        india_note="Global exchange. Accessible from India.",
        supported_quote=["USDT"],
    ),

    "Binance": ExchangeInfo(
        name="Binance",
        ccxt_id="binance",
        direct_url="https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol={symbol}",
        fee_maker=0.10,
        fee_taker=0.10,
        india_note="Public API accessible via Binance Vision mirror.",
        supported_quote=["USDT"],
    ),

    "KuCoin": ExchangeInfo(
        name="KuCoin",
        ccxt_id="kucoin",
        direct_url=None,
        fee_maker=0.10,
        fee_taker=0.10,
        india_note="Global exchange. Public API accessible.",
        supported_quote=["USDT"],
    ),

    "OKX": ExchangeInfo(
        name="OKX",
        ccxt_id="okx",
        direct_url=None,
        fee_maker=0.08,
        fee_taker=0.10,
        india_note="Global exchange. Public API accessible.",
        supported_quote=["USDT"],
    ),

    "Kraken": ExchangeInfo(
        name="Kraken",
        ccxt_id="kraken",
        direct_url=None,
        fee_maker=0.16,
        fee_taker=0.26,
        india_note="May be region restricted for Indian retail users.",
        supported_quote=["USDT"],
    ),

    "CoinDCX": ExchangeInfo(
        name="CoinDCX",
        ccxt_id=None,
        direct_url="https://api.coindcx.com/exchange/ticker",
        fee_maker=0.15,
        fee_taker=0.25,
        india_note="Indian exchange. INR and USDT pairs available.",
        supported_quote=["USDT", "INR"],
    ),

    "WazirX": ExchangeInfo(
        name="WazirX",
        ccxt_id=None,
        direct_url="https://api.wazirx.com/sapi/v1/tickers/24hr",
        fee_maker=0.20,
        fee_taker=0.20,
        india_note="Indian exchange. INR pairs available.",
        supported_quote=["USDT", "INR"],
    ),

    "ZebPay": ExchangeInfo(
        name="ZebPay",
        ccxt_id=None,
        direct_url="https://api.zebapi.com/market/ticker",
        fee_maker=0.15,
        fee_taker=0.25,
        india_note="Indian exchange. INR pairs available.",
        supported_quote=["USDT", "INR"],
    ),

    "Bitbns": ExchangeInfo(
        name="Bitbns",
        ccxt_id=None,
        direct_url="https://bitbns.com/order/getTickerWithVolume/",
        fee_maker=0.25,
        fee_taker=0.25,
        india_note="Indian exchange. INR pairs only.",
        supported_quote=["INR"],
    ),

    "Giottus": ExchangeInfo(
        name="Giottus",
        ccxt_id=None,
        direct_url="https://www.giottus.com/api/v1/tickers",
        fee_maker=0.25,
        fee_taker=0.25,
        india_note="Indian exchange. INR pairs.",
        supported_quote=["INR"],
    ),

    "Mudrex": ExchangeInfo(
        name="Mudrex",
        ccxt_id=None,
        direct_url="https://mudrex.com/api/v1/public/coins",
        fee_maker=0.00,
        fee_taker=0.00,
        india_note="Indian platform. Operates via partner exchanges.",
        supported_quote=["USDT", "INR"],
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# REUSABLE CCXT INSTANCES (module-level singleton, not per-request)
# ─────────────────────────────────────────────────────────────────────────────

_ccxt_instances: Dict[str, object] = {}

def _get_ccxt_instance(ccxt_id: str):
    """Return a cached CCXT instance, creating one if needed."""
    if ccxt_id not in _ccxt_instances:
        cls = getattr(ccxt, ccxt_id, None)
        if cls is None:
            return None
        opts = {
            "enableRateLimit": True,
            "timeout": 8000,
            "options": {
                "fetchCurrencies": False,
                "adjustForTimeDifference": True,
            },
        }
        if ccxt_id == "bybit":
            opts["options"]["defaultType"] = "spot"
        if ccxt_id == "kucoin":
            opts["options"]["fetchCurrencies"] = False
        instance = cls(opts)
        instance.has["fetchCurrencies"] = False
        _ccxt_instances[ccxt_id] = instance
    return _ccxt_instances[ccxt_id]


# ─────────────────────────────────────────────────────────────────────────────
# AVAILABILITY CACHE  (5-minute TTL)
# ─────────────────────────────────────────────────────────────────────────────

_availability_cache: Dict[str, dict] = {}
_AVAIL_TTL = 300  # seconds


def _cached_status(name: str) -> Optional[ExchangeStatus]:
    entry = _availability_cache.get(name)
    if entry and (time.time() - entry["ts"]) < _AVAIL_TTL:
        return entry["status"]
    return None


def _cache_status(name: str, status: ExchangeStatus):
    _availability_cache[name] = {"ts": time.time(), "status": status}


# ─────────────────────────────────────────────────────────────────────────────
# SSL CONTEXT (permissive, for CI/CD environments)
# ─────────────────────────────────────────────────────────────────────────────

def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# DIRECT REST PING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _ping_coindcx() -> tuple:
    """Returns (success: bool, latency_ms: float, error: str|None)"""
    try:
        t0 = time.time()
        req = urllib.request.Request(
            "https://api.coindcx.com/exchange/ticker",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(
            urllib.request.urlopen(req, context=_ssl_ctx(), timeout=5).read()
        )
        latency = (time.time() - t0) * 1000
        if isinstance(data, list) and len(data) > 0:
            return True, latency, None
        return False, latency, "Empty response"
    except Exception as e:
        return False, 0, str(e)[:120]


def _ping_wazirx() -> tuple:
    try:
        t0 = time.time()
        req = urllib.request.Request(
            "https://api.wazirx.com/sapi/v1/tickers/24hr",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(
            urllib.request.urlopen(req, context=_ssl_ctx(), timeout=5).read()
        )
        latency = (time.time() - t0) * 1000
        if isinstance(data, list) and len(data) > 0:
            return True, latency, None
        return False, latency, "Empty response"
    except Exception as e:
        return False, 0, str(e)[:120]


def _ping_zebpay() -> tuple:
    try:
        t0 = time.time()
        req = urllib.request.Request(
            "https://api.zebapi.com/market/ticker?symbol=BTC-INR",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(
            urllib.request.urlopen(req, context=_ssl_ctx(), timeout=5).read()
        )
        latency = (time.time() - t0) * 1000
        if data and isinstance(data, dict):
            return True, latency, None
        return False, latency, "Empty response"
    except Exception as e:
        return False, 0, str(e)[:120]


def _ping_bitbns() -> tuple:
    try:
        t0 = time.time()
        req = urllib.request.Request(
            "https://bitbns.com/order/getTickerWithVolume/",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(
            urllib.request.urlopen(req, context=_ssl_ctx(), timeout=5).read()
        )
        latency = (time.time() - t0) * 1000
        if data and isinstance(data, dict) and "BTC" in data:
            return True, latency, None
        return False, latency, "No BTC data"
    except Exception as e:
        return False, 0, str(e)[:120]


def _ping_giottus() -> tuple:
    try:
        t0 = time.time()
        req = urllib.request.Request(
            "https://www.giottus.com/api/v1/tickers",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(
            urllib.request.urlopen(req, context=_ssl_ctx(), timeout=5).read()
        )
        latency = (time.time() - t0) * 1000
        if data and isinstance(data, (dict, list)):
            return True, latency, None
        return False, latency, "Empty response"
    except Exception as e:
        return False, 0, str(e)[:120]


def _ping_mudrex() -> tuple:
    try:
        t0 = time.time()
        req = urllib.request.Request(
            "https://mudrex.com/api/v1/public/coins",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(
            urllib.request.urlopen(req, context=_ssl_ctx(), timeout=5).read()
        )
        latency = (time.time() - t0) * 1000
        if data:
            return True, latency, None
        return False, latency, "Empty response"
    except Exception as e:
        return False, 0, str(e)[:120]


# ─────────────────────────────────────────────────────────────────────────────
# CHECK SINGLE EXCHANGE AVAILABILITY
# ─────────────────────────────────────────────────────────────────────────────

_REGION_KEYWORDS = [
    "region", "restricted", "geo", "banned", "forbidden", "country",
    "unavailable in your country", "not supported in your region",
]

def _is_region_error(err: str) -> bool:
    e = err.lower()
    return any(k in e for k in _REGION_KEYWORDS)


def check_exchange_availability(name: str, force: bool = False) -> ExchangeStatus:
    """
    Check whether an exchange is reachable from the current network.
    Results are cached for 5 minutes.
    """
    if not force:
        cached = _cached_status(name)
        if cached:
            return cached

    info = EXCHANGE_REGISTRY.get(name)
    if not info:
        status = ExchangeStatus(name=name, status=STATUS_API_UNAVAILABLE, error="Unknown exchange")
        _cache_status(name, status)
        return status

    # ── Direct-API exchanges ──────────────────────────────────────────────────
    if name == "CoinDCX":
        ok, lat, err = _ping_coindcx()
        status = ExchangeStatus(
            name=name,
            status=STATUS_AVAILABLE if ok else STATUS_API_UNAVAILABLE,
            latency_ms=round(lat, 1),
            error=err,
            note=info.india_note,
        )
        _cache_status(name, status)
        return status

    if name == "WazirX":
        ok, lat, err = _ping_wazirx()
        status = ExchangeStatus(
            name=name,
            status=STATUS_AVAILABLE if ok else STATUS_API_UNAVAILABLE,
            latency_ms=round(lat, 1),
            error=err,
            note=info.india_note,
        )
        _cache_status(name, status)
        return status

    if name == "ZebPay":
        ok, lat, err = _ping_zebpay()
        status = ExchangeStatus(
            name=name,
            status=STATUS_AVAILABLE if ok else STATUS_API_UNAVAILABLE,
            latency_ms=round(lat, 1),
            error=err,
            note=info.india_note,
        )
        _cache_status(name, status)
        return status

    if name == "Bitbns":
        ok, lat, err = _ping_bitbns()
        status = ExchangeStatus(
            name=name,
            status=STATUS_AVAILABLE if ok else STATUS_API_UNAVAILABLE,
            latency_ms=round(lat, 1),
            error=err,
            note=info.india_note,
        )
        _cache_status(name, status)
        return status

    if name == "Giottus":
        ok, lat, err = _ping_giottus()
        status = ExchangeStatus(
            name=name,
            status=STATUS_AVAILABLE if ok else STATUS_API_UNAVAILABLE,
            latency_ms=round(lat, 1),
            error=err,
            note=info.india_note,
        )
        _cache_status(name, status)
        return status

    if name == "Mudrex":
        ok, lat, err = _ping_mudrex()
        status = ExchangeStatus(
            name=name,
            status=STATUS_AVAILABLE if ok else STATUS_API_UNAVAILABLE,
            latency_ms=round(lat, 1),
            error=err,
            note=info.india_note,
        )
        _cache_status(name, status)
        return status

    # ── CCXT exchanges ────────────────────────────────────────────────────────
    if info.ccxt_id:
        try:
            t0 = time.time()
            ex = _get_ccxt_instance(info.ccxt_id)
            if ex is None:
                raise RuntimeError(f"ccxt has no class: {info.ccxt_id}")

            # Lightweight public endpoint: fetch single ticker
            if info.ccxt_id == "binance":
                # Use Binance Vision mirror to avoid region blocks
                import ssl as _ssl
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                req = urllib.request.Request(
                    "https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                resp = json.loads(
                    urllib.request.urlopen(req, context=ctx, timeout=5).read()
                )
                if "price" not in resp:
                    raise ValueError("No price in Binance response")
            elif info.ccxt_id == "kraken":
                ex.fetch_ticker("BTC/USDT")
            else:
                ex.fetch_ticker("BTC/USDT")

            latency = (time.time() - t0) * 1000
            status = ExchangeStatus(
                name=name,
                status=STATUS_AVAILABLE,
                latency_ms=round(latency, 1),
                note=info.india_note,
            )

        except Exception as e:
            err_str = str(e)
            if _is_region_error(err_str):
                st = STATUS_REGION_RESTRICTED
            else:
                st = STATUS_API_UNAVAILABLE
            status = ExchangeStatus(
                name=name,
                status=st,
                error=err_str[:120],
                note=info.india_note,
            )

        _cache_status(name, status)
        return status

    # Fallback
    status = ExchangeStatus(name=name, status=STATUS_API_UNAVAILABLE, error="No fetch method")
    _cache_status(name, status)
    return status


# ─────────────────────────────────────────────────────────────────────────────
# CHECK ALL EXCHANGES (concurrent)
# ─────────────────────────────────────────────────────────────────────────────

def check_all_exchanges(force: bool = False) -> Dict[str, ExchangeStatus]:
    names = list(EXCHANGE_REGISTRY.keys())
    results = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(names)) as executor:
        future_map = {executor.submit(check_exchange_availability, n, force): n for n in names}
        for future in concurrent.futures.as_completed(future_map, timeout=15):
            name = future_map[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = ExchangeStatus(
                    name=name,
                    status=STATUS_API_UNAVAILABLE,
                    error=str(e)[:120],
                )

    return results


def get_available_exchanges() -> list:
    """Return list of exchange names that are currently available."""
    all_status = check_all_exchanges()
    return [name for name, s in all_status.items() if s.status == STATUS_AVAILABLE]
