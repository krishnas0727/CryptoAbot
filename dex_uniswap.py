# ============================================================
# DEX MODULE: UNISWAP V3 ON ARBITRUM / BASE
# ============================================================

import os
import sys
import time
import json
import urllib.request
import urllib.error
from typing import Dict, Optional, Any

# ============================================================
# PROXY CONFIGURATION
# ============================================================

proxy_url = (
    os.environ.get("EXCHANGE_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
)

# ============================================================
# NETWORK DEFINITIONS (Arbitrum One & Base)
# ============================================================

NETWORKS = {
    "arbitrum": {
        "name": "Arbitrum One",
        "chain_id": 42161,
        "rpc_urls": [
            "https://arb1.arbitrum.io/rpc",
            "https://arbitrum-one-rpc.publicnode.com",
            "https://1rpc.io/arb",
            "https://endpoints.omniatech.io/v1/arbitrum/one/public"
        ],
        "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "quoter_v1": "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
        "quoter_v2": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
        "swap_router": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "tokens": {
            "WETH": {"address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "decimals": 18},
            "USDT": {"address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "decimals": 6},
            "USDC": {"address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "decimals": 6},
            "WBTC": {"address": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", "decimals": 8},
            "BTC": {"address": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", "decimals": 8},
            "ETH": {"address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "decimals": 18},
        }
    },
    "base": {
        "name": "Base Mainnet",
        "chain_id": 8453,
        "rpc_urls": [
            "https://mainnet.base.org",
            "https://base-rpc.publicnode.com",
            "https://1rpc.io/base"
        ],
        "factory": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        "quoter_v1": "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",
        "quoter_v2": "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",
        "swap_router": "0x2626664c2603336E57B271c5C0b26F421741e481",
        "tokens": {
            "WETH": {"address": "0x4200000000000000000000000000000000000006", "decimals": 18},
            "USDC": {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6},
            "USDT": {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6},
            "cbBTC": {"address": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", "decimals": 8},
            "BTC": {"address": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", "decimals": 8},
            "ETH": {"address": "0x4200000000000000000000000000000000000006", "decimals": 18},
        }
    }
}

# ============================================================
# FUNCTION SELECTORS & ENCODING HELPERS
# ============================================================
# getPool(address,address,uint24) -> 0x1698ee82
GET_POOL_SEL = "1698ee82"
# slot0() -> 0x3850c7bd
SLOT0_SEL = "3850c7bd"
# quoteExactInputSingle(address,address,uint24,uint256,uint160) -> 0xf77db004
QUOTE_EXACT_INPUT_SINGLE_V1_SEL = "f77db004"
# balanceOf(address) -> 0x70a08231
BALANCE_OF_SEL = "70a08231"

def _pad32_address(addr: str) -> str:
    cleaned = addr.lower().replace("0x", "")
    return cleaned.rjust(64, "0")

def _pad32_uint(val: int) -> str:
    return hex(val)[2:].rjust(64, "0")

# ============================================================
# JSON-RPC CALL HELPER (with proxy & timeout)
# ============================================================

def eth_rpc_call(rpc_url: str, method: str, params: list, timeout: int = 4) -> Optional[Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) % 1000000,
        "method": method,
        "params": params
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    )

    try:
        handlers = []
        if proxy_url:
            handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        opener = urllib.request.build_opener(*handlers)
        with opener.open(req, timeout=timeout) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            if "result" in res_data:
                return res_data["result"]
    except Exception:
        pass
    return None

# ============================================================
# UNISWAP CLIENT
# ============================================================

class UniswapClient:
    def __init__(self, chain: str = "arbitrum", rpc_url: Optional[str] = None):
        self.chain = chain.lower() if chain in NETWORKS else "arbitrum"
        self.net_info = NETWORKS[self.chain]
        self.rpc_urls = [rpc_url] if rpc_url else self.net_info["rpc_urls"]
        self.cached_price = {}
        self.last_cache_time = 0

    def _rpc_request(self, method: str, params: list) -> Optional[Any]:
        for url in self.rpc_urls:
            res = eth_rpc_call(url, method, params)
            if res is not None:
                return res
        return None

    def get_token_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        sym = symbol.upper()
        return self.net_info["tokens"].get(sym)

    def get_spot_price(self, base_symbol: str = "BTC", quote_symbol: str = "USDT") -> Optional[float]:
        """
        Fetch real-time Uniswap V3 spot price using pool slot0() math or quoter.
        """
        now = time.time()
        cache_key = f"{base_symbol}/{quote_symbol}"
        if cache_key in self.cached_price and (now - self.last_cache_time) < 3.0:
            return self.cached_price[cache_key]

        base_info = self.get_token_info(base_symbol)
        quote_info = self.get_token_info(quote_symbol)

        if not base_info or not quote_info:
            return None

        addr_a = base_info["address"].lower()
        addr_b = quote_info["address"].lower()
        dec_a = base_info["decimals"]
        dec_b = quote_info["decimals"]

        factory_addr = self.net_info["factory"]
        fee_tiers = [500, 3000]  # 0.05%, 0.3%

        for fee in fee_tiers:
            calldata_pool = (
                "0x" +
                GET_POOL_SEL +
                _pad32_address(addr_a) +
                _pad32_address(addr_b) +
                _pad32_uint(fee)
            )

            pool_hex = self._rpc_request("eth_call", [{"to": factory_addr, "data": calldata_pool}, "latest"])
            if pool_hex and pool_hex != "0x" and len(pool_hex) >= 66:
                pool_addr_clean = "0x" + pool_hex[26:]
                if pool_addr_clean != "0x0000000000000000000000000000000000000000":
                    # Call slot0() on pool
                    slot0_hex = self._rpc_request("eth_call", [{"to": pool_addr_clean, "data": "0x" + SLOT0_SEL}, "latest"])
                    if slot0_hex and len(slot0_hex) >= 66:
                        try:
                            sqrtPriceX96 = int(slot0_hex[2:66], 16)
                            if sqrtPriceX96 > 0:
                                raw_price = (sqrtPriceX96 / (2 ** 96)) ** 2
                                # Determine token0 vs token1
                                if addr_a < addr_b:
                                    # token0 is base_symbol (e.g. WETH/WBTC), token1 is quote (e.g. USDT)
                                    price = raw_price * (10 ** (dec_a - dec_b))
                                else:
                                    # token0 is quote, token1 is base
                                    price = (1.0 / raw_price) * (10 ** (dec_a - dec_b))
                                
                                if price > 0:
                                    self.cached_price[cache_key] = price
                                    self.last_cache_time = now
                                    return price
                        except Exception:
                            pass

        return None

    def get_wallet_balances(self, wallet_address: str) -> Dict[str, float]:
        """
        Fetch on-chain balances (Native ETH, USDT, USDC, WBTC) via eth_call & eth_getBalance.
        """
        balances = {"USDT": 0.0, "USDC": 0.0, "BTC": 0.0, "ETH": 0.0, "WETH": 0.0, "free_usdt": 0.0, "free_btc": 0.0}
        if not wallet_address or not wallet_address.startswith("0x"):
            return balances

        # Native ETH balance
        eth_hex = self._rpc_request("eth_getBalance", [wallet_address, "latest"])
        if eth_hex:
            try:
                balances["ETH"] = int(eth_hex, 16) / 1e18
            except Exception:
                pass

        # Token balances
        for sym, token in self.net_info["tokens"].items():
            if sym in ["ETH"]:
                continue
            token_addr = token["address"]
            calldata = "0x" + BALANCE_OF_SEL + _pad32_address(wallet_address)
            res = self._rpc_request("eth_call", [{"to": token_addr, "data": calldata}, "latest"])
            if res and res != "0x":
                try:
                    raw_bal = int(res, 16)
                    bal = float(raw_bal) / (10 ** token["decimals"])
                    balances[sym] = bal
                    if sym in ["USDT", "USDC"]:
                        balances["free_usdt"] = max(balances.get("free_usdt", 0.0), bal)
                    if sym in ["BTC", "WBTC", "cbBTC"]:
                        balances["free_btc"] = max(balances.get("free_btc", 0.0), bal)
                except Exception:
                    pass

        return balances

    def estimate_swap_gas_cost_usd(self) -> float:
        """
        Estimate gas cost of a Uniswap swap in USD on Arbitrum/Base (~$0.01 - $0.08).
        """
        gas_hex = self._rpc_request("eth_gasPrice", [])
        if gas_hex:
            try:
                gas_price_wei = int(gas_hex, 16)
                estimated_gas = 160000
                cost_eth = (gas_price_wei * estimated_gas) / 1e18
                eth_price = self.get_spot_price("WETH", "USDT") or self.get_spot_price("WETH", "USDC") or 2500.0
                return round(cost_eth * eth_price, 4)
            except Exception:
                pass
        return 0.05


# ============================================================
# SINGLETON ACCESSORS
# ============================================================

_default_uniswap_client = None

def get_uniswap_client(chain: Optional[str] = None, rpc_url: Optional[str] = None) -> UniswapClient:
    global _default_uniswap_client
    target_chain = chain or os.getenv("DEX_CHAIN", "arbitrum")
    if _default_uniswap_client is None or _default_uniswap_client.chain != target_chain:
        _default_uniswap_client = UniswapClient(chain=target_chain, rpc_url=rpc_url)
    return _default_uniswap_client

def get_uniswap_live_price(symbol: str = "BTC/USDT") -> Optional[float]:
    parts = symbol.replace("-", "/").split("/")
    base_sym = parts[0] if len(parts) > 0 else "BTC"
    quote_sym = parts[1] if len(parts) > 1 else "USDT"
    client = get_uniswap_client()
    return client.get_spot_price(base_symbol=base_sym, quote_symbol=quote_sym)

def get_uniswap_balances(wallet_address: Optional[str] = None) -> Dict[str, float]:
    wallet = wallet_address or os.getenv("DEX_WALLET_ADDRESS", "")
    client = get_uniswap_client()
    return client.get_wallet_balances(wallet)

def estimate_uniswap_gas_cost_usd() -> float:
    client = get_uniswap_client()
    return client.estimate_swap_gas_cost_usd()
