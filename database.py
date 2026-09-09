import sqlite3
import os
import json
from datetime import datetime

from config import DATABASE_NAME, BACKUP_JSON_PATH


# ============================================================
# DATABASE FOLDER
# ============================================================

os.makedirs(
    os.path.dirname(DATABASE_NAME),
    exist_ok=True
)


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():

    conn = sqlite3.connect(
        DATABASE_NAME
    )

    conn.row_factory = sqlite3.Row

    return conn


# ============================================================
# CREATE DATABASE
# ============================================================

def create_database():

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            buy_exchange TEXT NOT NULL,

            sell_exchange TEXT NOT NULL,

            buy_price REAL NOT NULL,

            sell_price REAL NOT NULL,

            fees REAL NOT NULL,

            profit REAL NOT NULL,

            buy_order_id TEXT DEFAULT '',

            sell_order_id TEXT DEFAULT '',

            btc_amount REAL DEFAULT 0.0,

            slippage REAL DEFAULT 0.0,

            trade_amount REAL DEFAULT 0.0,

            created_at
                TIMESTAMP DEFAULT CURRENT_TIMESTAMP

        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY DEFAULT 1,
            usdt_balance REAL NOT NULL,
            binance_usdt REAL NOT NULL,
            bybit_usdt REAL NOT NULL,
            kraken_usdt REAL NOT NULL,
            binance_btc REAL DEFAULT 0.0,
            bybit_btc REAL DEFAULT 0.0,
            kraken_btc REAL DEFAULT 0.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migrate columns if existing tables lack new columns
    cursor.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in cursor.fetchall()]

    for col, col_type in [
        ("buy_order_id", "TEXT DEFAULT ''"),
        ("sell_order_id", "TEXT DEFAULT ''"),
        ("btc_amount", "REAL DEFAULT 0.0"),
        ("slippage", "REAL DEFAULT 0.0"),
        ("trade_amount", "REAL DEFAULT 0.0"),
        ("mode", "TEXT DEFAULT 'PAPER'")
    ]:
        if col not in columns:
            cursor.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")


    cursor.execute("PRAGMA table_info(portfolio)")
    port_cols = [row[1] for row in cursor.fetchall()]
    if "coinbase_usdt" not in port_cols:
        cursor.execute("ALTER TABLE portfolio ADD COLUMN coinbase_usdt REAL DEFAULT 3333.34")
    if "coinbase_btc" not in port_cols:
        cursor.execute("ALTER TABLE portfolio ADD COLUMN coinbase_btc REAL DEFAULT 0.0")

    # Seed portfolio if empty
    cursor.execute("SELECT COUNT(*) FROM portfolio")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO portfolio (id, usdt_balance, binance_usdt, bybit_usdt, kraken_usdt, coinbase_usdt)
            VALUES (1, 0.00, 0.00, 0.00, 0.00, 0.00)
        """)


    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            exchange TEXT PRIMARY KEY,
            api_key TEXT NOT NULL,
            api_secret TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

    # Auto-restore trades from JSON backup if database was wiped on server restart
    restore_trades_from_json_backup()


def save_bot_setting(key, value):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            INSERT INTO bot_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
        """, (str(key), json.dumps(value)))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Error saving setting {key}: {e}", flush=True)


def load_all_bot_settings():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("SELECT key, value FROM bot_settings")
        rows = cursor.fetchall()
        conn.close()
        settings = {}
        for row in rows:
            try:
                settings[row["key"]] = json.loads(row["value"])
            except Exception:
                settings[row["key"]] = row["value"]
        return settings
    except Exception as e:
        print(f"⚠️ Error loading bot settings: {e}", flush=True)
        return {}


def sync_trades_to_json_backup():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades ORDER BY id ASC")
        rows = cursor.fetchall()
        conn.close()

        trades_list = [dict(row) for row in rows]
        with open(BACKUP_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(trades_list, f, indent=2)
    except Exception as e:
        print(f"⚠️ JSON backup sync error: {e}", flush=True)


def restore_trades_from_json_backup():
    if not os.path.exists(BACKUP_JSON_PATH):
        return

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trades")
        count = cursor.fetchone()[0]

        if count == 0:
            with open(BACKUP_JSON_PATH, "r", encoding="utf-8") as f:
                backup_trades = json.load(f)

            if backup_trades and isinstance(backup_trades, list) and len(backup_trades) > 0:
                print(f"📦 Restoring {len(backup_trades)} trades from persistent JSON backup...", flush=True)
                for trade in backup_trades:
                    cursor.execute("""
                        INSERT INTO trades (
                            buy_exchange, sell_exchange, buy_price, sell_price, fees, profit,
                            buy_order_id, sell_order_id, btc_amount, slippage, trade_amount, mode, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        trade.get("buy_exchange") or trade.get("buy", ""),
                        trade.get("sell_exchange") or trade.get("sell", ""),
                        float(trade.get("buy_price", 0.0)),
                        float(trade.get("sell_price", 0.0)),
                        float(trade.get("fees", 0.0)),
                        float(trade.get("profit", 0.0)),
                        trade.get("buy_order_id", ""),
                        trade.get("sell_order_id", ""),
                        float(trade.get("btc_amount", 0.0)),
                        float(trade.get("slippage", 0.0)),
                        float(trade.get("trade_amount", 0.0)),
                        trade.get("mode", "PAPER"),
                        trade.get("created_at") or datetime.now().astimezone().isoformat()
                    ))
                conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Restore trades from JSON backup error: {e}", flush=True)


# ============================================================
# API KEY MANAGEMENT
# ============================================================

def save_api_key(exchange, api_key, api_secret):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO api_keys (exchange, api_key, api_secret, is_active, updated_at)
        VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(exchange) DO UPDATE SET
            api_key = excluded.api_key,
            api_secret = excluded.api_secret,
            is_active = 1,
            updated_at = CURRENT_TIMESTAMP
    """, (exchange.capitalize(), api_key.strip(), api_secret.strip()))
    conn.commit()
    conn.close()


def get_api_key(exchange):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM api_keys WHERE LOWER(exchange) = LOWER(?) AND is_active = 1", (exchange,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)

    # Environment variable fallback (e.g. BINANCE_API_KEY, BINANCE_API_SECRET)
    ex_upper = exchange.upper()
    env_key = os.environ.get(f"{ex_upper}_API_KEY") or os.environ.get(f"{ex_upper}_KEY")
    env_secret = os.environ.get(f"{ex_upper}_API_SECRET") or os.environ.get(f"{ex_upper}_SECRET")
    if env_key and env_secret:
        return {"exchange": exchange, "api_key": env_key.strip(), "api_secret": env_secret.strip(), "is_active": 1}

    return None


def get_all_api_keys():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT exchange, api_key, is_active, updated_at FROM api_keys")
    rows = cursor.fetchall()
    conn.close()
    result = {}
    for row in rows:
        r = dict(row)
        raw_k = r["api_key"]
        masked = raw_k[:4] + "..." + raw_k[-4:] if len(raw_k) > 8 else "****"
        data = {
            "api_key_masked": masked,
            "has_key": True,
            "updated_at": r["updated_at"]
        }
        result[r["exchange"].capitalize()] = data
        result[r["exchange"].lower()] = data
    return result



def delete_api_key(exchange):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM api_keys WHERE exchange = ?", (exchange.capitalize(),))
    conn.commit()
    conn.close()



# ============================================================
# PORTFOLIO MANAGEMENT
# ============================================================

def get_portfolio():

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("SELECT * FROM portfolio WHERE id = 1")

    row = cursor.fetchone()

    conn.close()

    if row:
        d = dict(row)
        if "coinbase_usdt" not in d:
            d["coinbase_usdt"] = d.get("kraken_usdt", 3333.34)
        if "coinbase_btc" not in d:
            d["coinbase_btc"] = d.get("kraken_btc", 0.0)
        return d

    return {
        "usdt_balance": 0.00,
        "binance_usdt": 0.00,
        "bybit_usdt": 0.00,
        "kraken_usdt": 0.00,
        "coinbase_usdt": 0.00,
        "binance_btc": 0.0,
        "bybit_btc": 0.0,
        "kraken_btc": 0.0,
        "coinbase_btc": 0.0
    }


def update_portfolio(portfolio):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        UPDATE portfolio
        SET usdt_balance = ?,
            binance_usdt = ?,
            bybit_usdt = ?,
            kraken_usdt = ?,
            coinbase_usdt = ?,
            binance_btc = ?,
            bybit_btc = ?,
            kraken_btc = ?,
            coinbase_btc = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
    """, (
        float(portfolio.get("usdt_balance", 0.0)),
        float(portfolio.get("binance_usdt", 0.0)),
        float(portfolio.get("bybit_usdt", 0.0)),
        float(portfolio.get("kraken_usdt", 0.0)),
        float(portfolio.get("coinbase_usdt", 0.0)),
        float(portfolio.get("binance_btc", 0.0)),
        float(portfolio.get("bybit_btc", 0.0)),
        float(portfolio.get("kraken_btc", 0.0)),
        float(portfolio.get("coinbase_btc", 0.0))
    ))

    conn.commit()

    conn.close()


def reset_portfolio(initial_usdt=0.00):

    per_ex = round(initial_usdt / 2, 2) if initial_usdt > 0 else 0.00

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        UPDATE portfolio
        SET usdt_balance = ?,
            binance_usdt = ?,
            bybit_usdt = ?,
            kraken_usdt = ?,
            coinbase_usdt = ?,
            binance_btc = 0.0,
            bybit_btc = 0.0,
            kraken_btc = 0.0,
            coinbase_btc = 0.0,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
    """, (initial_usdt, per_ex, per_ex, per_ex, per_ex))

    conn.commit()

    conn.close()


# ============================================================
# SAVE TRADE
# ============================================================

def save_trade(trade):

    create_database()

    conn = get_connection()

    cursor = conn.cursor()

    created_at_str = trade.get("created_at")
    if not created_at_str:
        created_at_str = datetime.now().astimezone().isoformat()

    cursor.execute("""
        INSERT INTO trades
        (
            buy_exchange,
            sell_exchange,
            buy_price,
            sell_price,
            fees,
            profit,
            buy_order_id,
            sell_order_id,
            btc_amount,
            slippage,
            trade_amount,
            mode,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade.get("buy_exchange") or trade.get("buy", ""),
        trade.get("sell_exchange") or trade.get("sell", ""),
        float(trade["buy_price"]),
        float(trade["sell_price"]),
        float(trade["fees"]),
        float(trade["profit"]),
        trade.get("buy_order_id", ""),
        trade.get("sell_order_id", ""),
        float(trade.get("btc_amount", 0.0)),
        float(trade.get("slippage", 0.0)),
        float(trade.get("trade_amount", 0.0)),
        trade.get("mode", "PAPER"),
        created_at_str
    ))

    conn.commit()
    conn.close()

    # Sync to persistent JSON backup file
    sync_trades_to_json_backup()


# ============================================================
# GET ALL TRADES
# ============================================================

def get_all_trades():

    create_database()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            buy_exchange,
            sell_exchange,
            buy_price,
            sell_price,
            fees,
            profit,
            buy_order_id,
            sell_order_id,
            btc_amount,
            slippage,
            trade_amount,
            COALESCE(mode, 'PAPER') AS mode,
            created_at
        FROM trades
        ORDER BY id DESC
    """)

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        d = dict(row)
        d["buy"] = d.get("buy_exchange", "")
        d["sell"] = d.get("sell_exchange", "")
        result.append(d)

    return result


def get_latest_trade():
    create_database()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            buy_exchange,
            sell_exchange,
            buy_price,
            sell_price,
            fees,
            profit,
            buy_order_id,
            sell_order_id,
            btc_amount,
            slippage,
            trade_amount,
            COALESCE(mode, 'PAPER') AS mode,
            created_at
        FROM trades
        ORDER BY id DESC
        LIMIT 1
    """)

    row = cursor.fetchone()
    conn.close()

    if row:
        d = dict(row)
        d["buy"] = d.get("buy_exchange", "")
        d["sell"] = d.get("sell_exchange", "")
        return d

    return None



# ============================================================
# DELETE ALL TRADES
# ============================================================

def delete_all_trades():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trades")
    conn.commit()
    conn.close()

    if os.path.exists(BACKUP_JSON_PATH):
        try:
            with open(BACKUP_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump([], f)
        except Exception as e:
            print(f"Error clearing JSON backup: {e}", flush=True)

    print("All old trades deleted successfully.", flush=True)


# ============================================================
# TOTAL PROFIT
# ============================================================

def get_total_profit():

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COALESCE(
                SUM(profit),
                0
            )
        FROM trades
    """)

    result = cursor.fetchone()[0]

    conn.close()

    return round(
        float(result),
        2
    )


# ============================================================
# TOTAL TRADES
# ============================================================

def get_total_trades():

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COUNT(*)
        FROM trades
    """)

    result = cursor.fetchone()[0]

    conn.close()

    return int(result)