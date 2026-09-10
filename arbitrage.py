
# ============================================================
# REAL ARBITRAGE ENGINE
# Binance + Bybit
#
# NO PAPER TRADING
# NO SIMULATION
# NO FAKE PROFIT
# ============================================================

import time

import config

from database import (
    create_database,
    save_trade,
)

from exchange import (
    get_live_prices,
    execute_live_real_trade,
)


# ============================================================
# STATE
# ============================================================

last_trade_time = 0
last_trade_key = None


# ============================================================
# ANALYZE MARKET
# ============================================================

def analyze_market():

    try:
        prices = get_live_prices()
    except Exception as e:

        print(f"❌ Failed to fetch live prices: {e}")

        return None

    # --------------------------------------------------------
    # Need at least 2 exchanges
    # --------------------------------------------------------

    if len(prices) < 2:
        return None

    # --------------------------------------------------------
    # LOWEST PRICE = BUY
    # HIGHEST PRICE = SELL
    # --------------------------------------------------------

    buy_exchange = min(
        prices,
        key=prices.get
    )

    sell_exchange = max(
        prices,
        key=prices.get
    )

    buy_price = float(
        prices[buy_exchange]
    )

    sell_price = float(
        prices[sell_exchange]
    )

    # --------------------------------------------------------
    # BASIC VALIDATION
    # --------------------------------------------------------

    if buy_price <= 0 or sell_price <= 0:
        return None

    if sell_price <= buy_price:

        difference = 0.0
        spread_percent = 0.0

    else:

        difference = (
            sell_price -
            buy_price
        )

        spread_percent = (
            difference /
            buy_price *
            100
        )

    # ========================================================
    # TRADE AMOUNT
    # ========================================================

    trade_amount = float(
        config.DEFAULT_TRADE_AMOUNT
    )

    # Never exceed configured maximum.
    trade_amount = min(
        trade_amount,
        float(config.MAX_TRADE_AMOUNT_USDT)
    )

    # --------------------------------------------------------
    # Minimum trade amount
    # --------------------------------------------------------

    if trade_amount < float(config.MIN_TRADE_USDT):

        trade_amount = float(
            config.MIN_TRADE_USDT
        )

    # ========================================================
    # BTC AMOUNT
    # ========================================================

    btc_amount = (
        trade_amount /
        buy_price
    )

    # ========================================================
    # ESTIMATED FEES
    # ========================================================

    fee_rate = (
        float(config.ESTIMATED_FEE_PERCENT)
        / 100
    )

    estimated_buy_cost = (
        btc_amount *
        buy_price
    )

    estimated_sell_value = (
        btc_amount *
        sell_price
    )

    estimated_buy_fee = (
        estimated_buy_cost *
        fee_rate
    )

    estimated_sell_fee = (
        estimated_sell_value *
        fee_rate
    )

    # --------------------------------------------------------
    # DEX Gas Fee Calculation (if Uniswap involved)
    # --------------------------------------------------------
    dex_gas_cost_usd = 0.0
    if buy_exchange.lower() == "uniswap" or sell_exchange.lower() == "uniswap":
        try:
            import dex_uniswap
            dex_gas_cost_usd = dex_uniswap.estimate_uniswap_gas_cost_usd()
        except Exception:
            dex_gas_cost_usd = 0.05

    total_estimated_fees = (
        estimated_buy_fee +
        estimated_sell_fee +
        dex_gas_cost_usd
    )

    # ========================================================
    # SLIPPAGE
    # ========================================================

    if config.SLIPPAGE_ENABLED:

        slip_rate = (
            float(config.SLIPPAGE_PCT)
            / 100
        )

    else:

        slip_rate = 0.0

    # Worst case:
    # BUY becomes more expensive
    # SELL becomes cheaper

    worst_buy_price = (
        buy_price *
        (1 + slip_rate)
    )

    worst_sell_price = (
        sell_price *
        (1 - slip_rate)
    )

    worst_buy_cost = (
        btc_amount *
        worst_buy_price
    )

    worst_sell_value = (
        btc_amount *
        worst_sell_price
    )

    worst_buy_fee = (
        worst_buy_cost *
        fee_rate
    )

    worst_sell_fee = (
        worst_sell_value *
        fee_rate
    )

    # ========================================================
    # WORST CASE NET PROFIT
    # ========================================================

    worst_net_profit = (
        worst_sell_value
        - worst_buy_cost
        - worst_buy_fee
        - worst_sell_fee
        - dex_gas_cost_usd
    )

    # --------------------------------------------------------
    # Net profit percentage
    # --------------------------------------------------------

    if worst_buy_cost > 0:

        net_profit_percent = (
            worst_net_profit /
            worst_buy_cost *
            100
        )

    else:

        net_profit_percent = 0.0

    # ========================================================
    # PROFITABILITY CHECK
    # ========================================================

    profitable_by_usdt = (
        worst_net_profit >=
        float(config.MIN_PROFIT)
    )

    profitable_by_percent = (
        net_profit_percent >=
        float(config.MIN_PROFIT_PERCENT)
    )

    profitable = (
        profitable_by_usdt
        and profitable_by_percent
    )

    # ========================================================
    # RETURN MARKET DATA
    # ========================================================

    return {

        "prices":
            prices,

        "buy_exchange":
            buy_exchange,

        "sell_exchange":
            sell_exchange,

        "buy_price":
            round(
                buy_price,
                8
            ),

        "sell_price":
            round(
                sell_price,
                8
            ),

        "difference":
            round(
                difference,
                8
            ),

        "spread_percent":
            round(
                spread_percent,
                8
            ),

        "trade_amount":
            round(
                trade_amount,
                8
            ),

        "btc_amount":
            round(
                btc_amount,
                12
            ),

        "buy_fee":
            round(
                worst_buy_fee,
                8
            ),

        "sell_fee":
            round(
                worst_sell_fee,
                8
            ),

        "fees":
            round(
                worst_buy_fee +
                worst_sell_fee,
                8
            ),

        "net_profit":
            round(
                worst_net_profit,
                8
            ),

        "net_profit_percent":
            round(
                net_profit_percent,
                8
            ),

        "minimum_profit":
            float(config.MIN_PROFIT),

        "minimum_profit_percent":
            float(config.MIN_PROFIT_PERCENT),

        "profitable":
            profitable,

        "auto_trade_enabled":
            config.AUTO_TRADE_ENABLED,

        "live_trading_armed":
            config.LIVE_TRADING_ARMED,

        "trading_mode":
            config.TRADING_MODE,
    }


# ============================================================
# EXECUTE REAL TRADE
# ============================================================

def execute_real_trade(
    market_data,
    custom_amount=None,
    is_manual=False
):

    global last_trade_time
    global last_trade_key

    # ========================================================
    # LIVE ONLY
    # ========================================================

    if config.TRADING_MODE != "LIVE":

        return {

            "success": False,

            "message":
                "Only LIVE trading is supported.",
        }

    # ========================================================
    # MARKET DATA CHECK
    # ========================================================

    if not market_data:

        return {

            "success": False,

            "message":
                "No market data available.",
        }

    # ========================================================
    # AUTO / MANUAL CHECK
    # ========================================================

    if not is_manual:

        if not config.AUTO_TRADE_ENABLED:

            return {

                "success": False,

                "message":
                    (
                        "Automatic trading is disabled. "
                        "Use manual execution."
                    ),
            }

    # ========================================================
    # LIVE TRADING ARM CHECK
    # ========================================================

    if not config.LIVE_TRADING_ARMED:

        return {

            "success": False,

            "message":
                (
                    "LIVE TRADING IS NOT ARMED. "
                    "Set LIVE_TRADING_ARMED=True "
                    "only after read-only tests pass."
                ),
        }

    # ========================================================
    # EXCHANGE VALIDATION
    # ========================================================

    buy_exchange = (
        market_data.get(
            "buy_exchange"
        )
    )

    sell_exchange = (
        market_data.get(
            "sell_exchange"
        )
    )

    if not buy_exchange or not sell_exchange:

        return {

            "success": False,

            "message":
                "Invalid buy/sell exchange.",
        }

    # Only Binance and Bybit are allowed.

    if buy_exchange not in config.SUPPORTED_EXCHANGES:

        return {

            "success": False,

            "message":
                f"Unsupported buy exchange: {buy_exchange}",
        }

    if sell_exchange not in config.SUPPORTED_EXCHANGES:

        return {

            "success": False,

            "message":
                f"Unsupported sell exchange: {sell_exchange}",
        }

    if buy_exchange == sell_exchange:

        return {

            "success": False,

            "message":
                "Buy and sell exchanges cannot be the same.",
        }

    # ========================================================
    # PRICE VALIDATION
    # ========================================================

    buy_price = float(
        market_data.get(
            "buy_price",
            0
        )
    )

    sell_price = float(
        market_data.get(
            "sell_price",
            0
        )
    )

    if buy_price <= 0 or sell_price <= 0:

        return {

            "success": False,

            "message":
                "Invalid market prices.",
        }

    if sell_price <= buy_price:

        return {

            "success": False,

            "message":
                "No positive arbitrage spread.",
        }

    # ========================================================
    # PROFIT & SPREAD COMPUTATION
    # ========================================================

    net_profit = float(
        market_data.get(
            "net_profit",
            0
        )
    )

    net_profit_percent = float(
        market_data.get(
            "net_profit_percent",
            0
        )
    )

    # ========================================================
    # PROFIT CHECK (AUTOMATIC & MANUAL)
    # ========================================================

    if not is_manual:

        if net_profit < float(
            config.MIN_PROFIT
        ):

            return {

                "success": False,

                "message":
                    (
                        "Trade rejected: estimated "
                        "net profit is below minimum."
                    ),

                "net_profit":
                    net_profit,

                "minimum_profit":
                    float(config.MIN_PROFIT),
            }

        if net_profit_percent < float(
            config.MIN_PROFIT_PERCENT
        ):

            return {

                "success": False,

                "message":
                    (
                        "Trade rejected: estimated "
                        "net profit percentage is below minimum."
                    ),

                "net_profit_percent":
                    net_profit_percent,

                "minimum_profit_percent":
                    float(config.MIN_PROFIT_PERCENT),
            }

    # ========================================================
    # DUPLICATE / COOLDOWN PROTECTION
    # ========================================================

    current_time = time.time()

    trade_key = (
        f"{buy_exchange}:"
        f"{sell_exchange}"
    )

    if (
        trade_key == last_trade_key
        and (
            current_time -
            last_trade_time
        ) < float(
            config.AUTO_TRADE_COOLDOWN
        )
    ):

        remaining = max(
            0,
            int(
                config.AUTO_TRADE_COOLDOWN
                - (
                    current_time -
                    last_trade_time
                )
            )
        )

        return {

            "success": False,

            "message":
                (
                    f"Trade cooldown active. "
                    f"Wait {remaining} seconds."
                ),
        }

    # ========================================================
    # TRADE AMOUNT
    # ========================================================

    if custom_amount is not None:

        trade_amount = float(
            custom_amount
        )

    else:

        trade_amount = float(
            config.DEFAULT_TRADE_AMOUNT
        )

    # Never exceed maximum configured amount.

    trade_amount = min(
        trade_amount,
        float(config.MAX_TRADE_AMOUNT_USDT)
    )

    if trade_amount < float(
        config.MIN_TRADE_USDT
    ):

        return {

            "success": False,

            "message":
                (
                    f"Trade amount {trade_amount:.4f} USDT "
                    f"is below minimum "
                    f"{config.MIN_TRADE_USDT:.4f} USDT."
                ),
        }

    # ========================================================
    # REAL EXECUTION
    # ========================================================

    print()
    print(
        "=========================================="
    )
    print(
        "       REAL TRADE EXECUTION"
    )
    print(
        "=========================================="
    )

    print(
        f"BUY  : {buy_exchange}"
    )

    print(
        f"SELL : {sell_exchange}"
    )

    print(
        f"BUY PRICE  : {buy_price:.2f}"
    )

    print(
        f"SELL PRICE : {sell_price:.2f}"
    )

    print(
        f"AMOUNT     : {trade_amount:.4f} USDT"
    )

    print(
        f"EST. NET   : {net_profit:.8f} USDT"
    )

    print(
        "=========================================="
    )

    try:
        result = execute_live_real_trade(

            buy_exchange_name=
                buy_exchange,

            sell_exchange_name=
                sell_exchange,

            buy_price=
                buy_price,

            sell_price=
                sell_price,

            trade_amount=
                trade_amount,
        )
    except Exception as exec_err:
        return {
            "success": False,
            "message": f"Execution error: {str(exec_err)}"
        }

    # ========================================================
    # SUCCESS
    # ========================================================

    if result.get("success"):

        trade = result.get(
            "trade"
        )

        if trade:

            save_trade(
                trade
            )

        last_trade_time = (
            current_time
        )

        last_trade_key = (
            trade_key
        )

        return {

            "success": True,

            "message":
                "REAL LIVE TRADE EXECUTED.",

            "trade":
                trade,

            "buy_exchange":
                buy_exchange,

            "sell_exchange":
                sell_exchange,

            "trade_amount":
                trade_amount,

            "estimated_net_profit":
                net_profit,
        }

    # ========================================================
    # FAILURE
    # ========================================================

    return {

        "success": False,

        "message":
            result.get(
                "message",
                "Real trade failed."
            ),

        "recovery_required":
            result.get(
                "recovery_required",
                False
            ),

        "buy_order_id":
            result.get(
                "buy_order_id"
            ),

        "sell_order_id":
            result.get(
                "sell_order_id"
            ),

        "btc_amount":
            result.get(
                "btc_amount"
            ),

        "buy_exchange":
            buy_exchange,

        "sell_exchange":
            sell_exchange,
    }


# ============================================================
# CLI TEST
# ============================================================

if __name__ == "__main__":

    create_database()

    data = analyze_market()

    if not data:

        print(
            "Unable to obtain enough live prices."
        )

    else:

        print()
        print(
            "=========================================="
        )
        print(
            "       LIVE ARBITRAGE ANALYSIS"
        )
        print(
            "=========================================="
        )

        # ----------------------------------------------------
        # PRICES
        # ----------------------------------------------------

        print(
            f"Binance   : "
            f"{data['prices'].get('Binance', 'N/A')}"
        )

        print(
            f"Bybit     : "
            f"{data['prices'].get('Bybit', 'N/A')}"
        )

        print(
            "------------------------------------------"
        )

        # ----------------------------------------------------
        # BUY / SELL
        # ----------------------------------------------------

        print(
            f"BUY       : "
            f"{data['buy_exchange']}"
        )

        print(
            f"BUY PRICE : "
            f"{data['buy_price']:.2f}"
        )

        print(
            f"SELL      : "
            f"{data['sell_exchange']}"
        )

        print(
            f"SELL PRICE: "
            f"{data['sell_price']:.2f}"
        )

        # ----------------------------------------------------
        # SPREAD
        # ----------------------------------------------------

        print(
            f"SPREAD    : "
            f"{data['spread_percent']:.4f}%"
        )

        print(
            f"DIFFERENCE: "
            f"{data['difference']:.8f} USDT"
        )

        # ----------------------------------------------------
        # TRADE SIZE
        # ----------------------------------------------------

        print(
            f"TRADE SIZE: "
            f"{data['trade_amount']:.4f} USDT"
        )

        print(
            f"BTC AMOUNT: "
            f"{data['btc_amount']:.12f} BTC"
        )

        # ----------------------------------------------------
        # FEES
        # ----------------------------------------------------

        print(
            f"EST. FEES : "
            f"{data['fees']:.8f} USDT"
        )

        # ----------------------------------------------------
        # PROFIT
        # ----------------------------------------------------

        print(
            f"NET PROFIT: "
            f"{data['net_profit']:.8f} USDT"
        )

        print(
            f"NET %     : "
            f"{data['net_profit_percent']:.4f}%"
        )

        # ----------------------------------------------------
        # PROFIT STATUS
        # ----------------------------------------------------

        if data["profitable"]:

            print(
                "STATUS    : ✅ PROFITABLE"
            )

        else:

            print(
                "STATUS    : ❌ NOT PROFITABLE"
            )

        # ----------------------------------------------------
        # SAFETY STATUS
        # ----------------------------------------------------

        print(
            "------------------------------------------"
        )

        print(
            f"AUTO TRADE: "
            f"{config.AUTO_TRADE_ENABLED}"
        )

        print(
            f"LIVE ARM  : "
            f"{config.LIVE_TRADING_ARMED}"
        )

        print(
            f"MODE      : "
            f"{config.TRADING_MODE}"
        )

        print(
            "=========================================="
        )
