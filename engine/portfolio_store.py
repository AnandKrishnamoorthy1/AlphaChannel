"""SQLite persistence for AlphaChannel's governed paper portfolio account."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class PortfolioStore:
    """Persist account cash and equity positions on a local durable volume."""

    ACCOUNT_ID = "agentic-x-4821"
    SEED_VERSION = "5"

    SEED_POSITIONS = (
        ("NOW", "ServiceNow", "Software", 2.0, 104.10, 107.71),
        ("SNOW", "Snowflake", "Software", 5.0, 165.00, 180.00),
        ("NU", "Nu Holdings", "Financial Services", 40.0, 11.20, 14.00),
    )

    def __init__(self, db_path: str | Path | None = None) -> None:
        configured_path = db_path or os.getenv("ALPHACHANNEL_PORTFOLIO_DB_PATH", "data/portfolio.db")
        self.db_path = Path(configured_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS portfolio_accounts (
                    account_id TEXT PRIMARY KEY,
                    portfolio_name TEXT NOT NULL,
                    cash_balance REAL NOT NULL CHECK (cash_balance >= 0),
                    unallocated_buying_power REAL NOT NULL CHECK (unallocated_buying_power >= 0),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS portfolio_positions (
                    account_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    sector TEXT NOT NULL DEFAULT 'Unknown',
                    shares_held REAL NOT NULL CHECK (shares_held > 0),
                    average_buy_price REAL NOT NULL CHECK (average_buy_price > 0),
                    fallback_market_price REAL NOT NULL CHECK (fallback_market_price > 0),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (account_id, ticker),
                    FOREIGN KEY (account_id) REFERENCES portfolio_accounts(account_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS portfolio_metadata (
                    metadata_key TEXT PRIMARY KEY,
                    metadata_value TEXT NOT NULL
                );
                """
            )
            version_row = connection.execute(
                "SELECT metadata_value FROM portfolio_metadata WHERE metadata_key = 'seed_version'"
            ).fetchone()
            if version_row is None or str(version_row[0]) != self.SEED_VERSION:
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(portfolio_positions)").fetchall()
                }
                if "sector" not in columns:
                    connection.execute(
                        "ALTER TABLE portfolio_positions ADD COLUMN sector TEXT NOT NULL DEFAULT 'Unknown'"
                    )
                connection.execute("DELETE FROM portfolio_positions WHERE account_id = ?", (self.ACCOUNT_ID,))
                connection.execute("DELETE FROM portfolio_accounts WHERE account_id = ?", (self.ACCOUNT_ID,))
                connection.execute(
                    """
                    INSERT INTO portfolio_accounts
                        (account_id, portfolio_name, cash_balance, unallocated_buying_power)
                    VALUES (?, ?, ?, ?)
                    """,
                    (self.ACCOUNT_ID, "Robinhood Agentic Account #X-4821", 8_518.80, 8_518.80),
                )
                connection.executemany(
                    """
                    INSERT INTO portfolio_positions
                        (account_id, ticker, company_name, sector, shares_held, average_buy_price, fallback_market_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [(self.ACCOUNT_ID, *position) for position in self.SEED_POSITIONS],
                )
                connection.execute(
                    """
                    INSERT INTO portfolio_metadata (metadata_key, metadata_value)
                    VALUES ('seed_version', ?)
                    ON CONFLICT(metadata_key) DO UPDATE SET metadata_value = excluded.metadata_value
                    """,
                    (self.SEED_VERSION,),
                )

    def load(self) -> dict[str, Any]:
        with closing(self._connect()) as connection, connection:
            account = connection.execute(
                "SELECT * FROM portfolio_accounts WHERE account_id = ?",
                (self.ACCOUNT_ID,),
            ).fetchone()
            positions = connection.execute(
                "SELECT * FROM portfolio_positions WHERE account_id = ? ORDER BY ticker",
                (self.ACCOUNT_ID,),
            ).fetchall()
        if account is None:
            raise RuntimeError(f"Portfolio account {self.ACCOUNT_ID} is not initialized.")
        return {
            "account": dict(account),
            "positions": [dict(position) for position in positions],
        }

    def apply_dry_run_trade(
        self,
        *,
        ticker: str,
        side: str,
        notional_usd: float,
        reference_price: float | None = None,
        company_name: str | None = None,
        sector: str | None = None,
    ) -> dict[str, Any]:
        """Apply an approved paper-trade transition to the persisted portfolio."""
        normalized_ticker = ticker.strip().upper()
        if notional_usd <= 0:
            raise ValueError("Trade notional must be positive.")
        if side not in {"buy", "sell"}:
            return {"ticker": normalized_ticker, "side": side, "state_changed": False}

        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute(
                "SELECT * FROM portfolio_accounts WHERE account_id = ?",
                (self.ACCOUNT_ID,),
            ).fetchone()
            position = connection.execute(
                "SELECT * FROM portfolio_positions WHERE account_id = ? AND ticker = ?",
                (self.ACCOUNT_ID, normalized_ticker),
            ).fetchone()
            if account is None:
                raise RuntimeError("Portfolio account is not initialized.")

            cash_balance = float(account["cash_balance"])
            if side == "buy":
                price = (
                    float(reference_price)
                    if reference_price is not None and reference_price > 0
                    else float(position["fallback_market_price"])
                    if position
                    else 0.0
                )
                if price <= 0:
                    raise ValueError(f"No reference price is available for {normalized_ticker}.")
                if notional_usd > cash_balance:
                    raise ValueError(f"Insufficient buying power for ${notional_usd:,.2f} {normalized_ticker} purchase.")
                shares_added = notional_usd / price
                if position:
                    old_shares = float(position["shares_held"])
                    old_average = float(position["average_buy_price"])
                    new_shares = old_shares + shares_added
                    new_average = ((old_shares * old_average) + notional_usd) / new_shares
                    connection.execute(
                        """
                        UPDATE portfolio_positions
                        SET shares_held = ?, average_buy_price = ?, fallback_market_price = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE account_id = ? AND ticker = ?
                        """,
                        (new_shares, new_average, price, self.ACCOUNT_ID, normalized_ticker),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO portfolio_positions
                            (account_id, ticker, company_name, sector, shares_held, average_buy_price, fallback_market_price)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self.ACCOUNT_ID,
                            normalized_ticker,
                            company_name or normalized_ticker,
                            sector or "Unknown",
                            shares_added,
                            price,
                            price,
                        ),
                    )
                shares_changed = shares_added
            else:
                if position is None:
                    raise ValueError(f"No position exists for {normalized_ticker}.")
                price = (
                    float(reference_price)
                    if reference_price is not None and reference_price > 0
                    else float(position["fallback_market_price"])
                )
                shares_available = float(position["shares_held"])
                shares_changed = min(shares_available, notional_usd / price)
                proceeds = shares_changed * price
                remaining_shares = shares_available - shares_changed
                if remaining_shares <= 1e-8:
                    connection.execute(
                        "DELETE FROM portfolio_positions WHERE account_id = ? AND ticker = ?",
                        (self.ACCOUNT_ID, normalized_ticker),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE portfolio_positions
                        SET shares_held = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE account_id = ? AND ticker = ?
                        """,
                        (remaining_shares, self.ACCOUNT_ID, normalized_ticker),
                    )
                notional_usd = proceeds

            new_cash = cash_balance - notional_usd if side == "buy" else cash_balance + notional_usd
            connection.execute(
                """
                UPDATE portfolio_accounts
                SET cash_balance = ?, unallocated_buying_power = ?, updated_at = CURRENT_TIMESTAMP
                WHERE account_id = ?
                """,
                (new_cash, new_cash, self.ACCOUNT_ID),
            )

        return {
            "ticker": normalized_ticker,
            "side": side,
            "notional_usd": round(notional_usd, 2),
            "shares_changed": round(shares_changed, 8),
            "cash_balance": round(new_cash, 2),
            "state_changed": True,
        }
