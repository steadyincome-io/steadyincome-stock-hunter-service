"""
Database Helper Module for SPX 0DTE Credit Spread Strategy
Manages SQLite storage in spx_0dte_strategy/spx_spread_trades.db
"""

import os
import sqlite3
import pandas as pd
import datetime

DB_PATH = 'spx_0dte_strategy/spx_spread_trades.db'

def init_db():
    """Create trades table if it does not exist"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spx_spread_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            spx_open REAL NOT NULL,
            spx_entry REAL NOT NULL,
            open_to_entry_return REAL NOT NULL,
            direction TEXT NOT NULL,
            short_strike REAL NOT NULL,
            long_strike REAL NOT NULL,
            target_delta REAL NOT NULL,
            spread_width REAL NOT NULL,
            short_symbol TEXT,
            long_symbol TEXT,
            entry_credit REAL NOT NULL,
            target_exit_debit REAL NOT NULL,
            exit_time TEXT,
            exit_spread_price REAL,
            exit_reason TEXT,
            gross_pnl REAL,
            net_pnl REAL,
            max_profit REAL,
            max_loss REAL,
            win BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def log_trade_entry(trade_dict):
    """Insert a new trade record into SQLite"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO spx_spread_trades (
            trade_date, entry_time, spx_open, spx_entry, open_to_entry_return,
            direction, short_strike, long_strike, target_delta, spread_width,
            short_symbol, long_symbol, entry_credit, target_exit_debit,
            max_profit, max_loss
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        trade_dict['trade_date'], trade_dict['entry_time'], trade_dict['spx_open'],
        trade_dict['spx_entry'], trade_dict['open_to_entry_return'], trade_dict['direction'],
        trade_dict['short_strike'], trade_dict['long_strike'], trade_dict['target_delta'],
        trade_dict['spread_width'], trade_dict['short_symbol'], trade_dict['long_symbol'],
        trade_dict['entry_credit'], trade_dict['target_exit_debit'], trade_dict['max_profit'],
        trade_dict['max_loss']
    ))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id

def update_trade_exit(trade_id, exit_time, exit_spread_price, exit_reason, gross_pnl, net_pnl, win):
    """Update trade record upon exit"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE spx_spread_trades SET
            exit_time = ?,
            exit_spread_price = ?,
            exit_reason = ?,
            gross_pnl = ?,
            net_pnl = ?,
            win = ?
        WHERE id = ?
    ''', (exit_time, exit_spread_price, exit_reason, gross_pnl, net_pnl, win, trade_id))
    conn.commit()
    conn.close()

def get_all_trades_df():
    """Retrieve all recorded live trades as pandas DataFrame"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM spx_spread_trades ORDER BY id DESC", conn)
    conn.close()
    return df

if __name__ == '__main__':
    init_db()
    print(f"Database initialized at {DB_PATH}")
