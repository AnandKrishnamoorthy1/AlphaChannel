"""
Mock Simulation Engine - Paper Trading for Public/Demo Users

Provides a high-fidelity trading simulation that:
- Uses REAL Alpha Vantage prices (so it feels authentic)
- Tracks simulated portfolio in JSON file
- Returns responses matching Robinhood's schema
- Prevents accidental real money execution

Perfect for:
- Public demos on GitHub
- Teaching how the system works
- Testing without financial risk
- Multi-tenant SaaS deployments
"""

import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional
from loguru import logger

from app.trading.broker_interface import (
    BaseBroker, 
    TradeOrder, 
    AccountInfo, 
    OrderSide, 
    OrderType, 
    OrderStatus
)
from app.tools.external_tools import FinancialDataTools


class MockSimulationEngine(BaseBroker):
    """
    High-fidelity paper trading engine for simulated trading.
    
    Uses real Alpha Vantage prices but simulated account state.
    Ideal for public demos, testing, and risk-free experimentation.
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize the mock simulation engine.
        
        Args:
            db_path: Path to JSON file for simulation ledger. 
                    Defaults to ./simulation_ledger.json
        """
        self.db_path = db_path or Path("simulation_ledger.json")
        self.financial_tools = FinancialDataTools()
        
        # Load or initialize simulation ledger
        self._load_or_init_ledger()
        logger.info(f"🎮 Mock Simulation Engine initialized")
        logger.info(f"   Ledger: {self.db_path}")
        logger.info(f"   Accounts: {len(self.ledger.get('accounts', {}))}")
    
    def _load_or_init_ledger(self):
        """Load existing simulation ledger or create new one with default portfolio."""
        if self.db_path.exists():
            try:
                with open(self.db_path, 'r') as f:
                    self.ledger = json.load(f)
                logger.debug(f"Loaded existing simulation ledger")
            except Exception as e:
                logger.warning(f"Failed to load ledger: {e}. Creating new one with defaults.")
                self.ledger = self._create_ledger_with_defaults()
        else:
            self.ledger = self._create_ledger_with_defaults()
    
    def _create_ledger_with_defaults(self) -> Dict:
        """Create simulation ledger initialized with default portfolio."""
        try:
            default_portfolio_path = Path(__file__).parent.parent / "config" / "default_portfolio.json"
            if default_portfolio_path.exists():
                with open(default_portfolio_path, 'r') as f:
                    default_data = json.load(f)
                
                # Convert default portfolio to ledger format
                account_id = default_data.get("account_id", "default")
                positions = {}
                for ticker, pos_data in default_data.get("positions", {}).items():
                    positions[ticker] = {
                        "ticker": ticker,
                        "shares": pos_data["shares"],
                        "entry_price": pos_data["entry_price"],
                        "current_price": pos_data["current_price"],
                        "quantity": pos_data["shares"]
                    }
                
                return {
                    "accounts": {
                        account_id: {
                            "id": account_id,
                            "account_type": "paper_trading",
                            "cash_balance": default_data.get("cash_balance", 10000.0),
                            "portfolio_value": default_data.get("cash_balance", 10000.0),
                            "positions": positions,
                            "created_at": datetime.now().isoformat(),
                        }
                    },
                    "orders": [],
                    "positions": positions,
                }
            else:
                logger.warning(f"Default portfolio not found at {default_portfolio_path}")
                return self._create_blank_ledger()
        except Exception as e:
            logger.error(f"Failed to load default portfolio: {e}")
            return self._create_blank_ledger()
    
    def _create_blank_ledger(self) -> Dict:
        """Create a blank simulation ledger structure."""
        return {
            "accounts": {},
            "orders": [],
            "positions": {},
        }
    
    def _save_ledger(self):
        """Persist ledger to JSON file."""
        try:
            with open(self.db_path, 'w') as f:
                json.dump(self.ledger, f, indent=2, default=str)
            logger.debug(f"Saved simulation ledger")
        except Exception as e:
            logger.error(f"Failed to save ledger: {e}")
    
    def _ensure_account_exists(self, account_id: str):
        """Create account if it doesn't exist (with default $10k balance)."""
        if account_id not in self.ledger["accounts"]:
            self.ledger["accounts"][account_id] = {
                "id": account_id,
                "account_type": "paper_trading",
                "cash_balance": 10000.0,  # Default $10k starting balance
                "portfolio_value": 10000.0,
                "positions": {},
                "created_at": datetime.now().isoformat(),
            }
            self._save_ledger()
            logger.info(f"Created new simulated account: {account_id} (balance: $10,000)")
    
    async def get_account_info(self, account_id: str) -> AccountInfo:
        """
        Get simulated account information.
        
        Args:
            account_id: The simulated account ID
            
        Returns:
            AccountInfo with simulated balance and positions
        """
        self._ensure_account_exists(account_id)
        account = self.ledger["accounts"][account_id]

        # Use stored portfolio_value to avoid burning AV quota here.
        # The portfolio endpoint fetches live prices separately.
        positions = account.get("positions", {})
        total_value = account.get("portfolio_value", account["cash_balance"])

        return AccountInfo(
            account_id=account_id,
            account_type="paper_trading",
            buying_power=account["cash_balance"],  # In simulation, cash = buying power
            portfolio_value=total_value,
            cash_balance=account["cash_balance"],
            positions_count=len(positions),
            is_simulated=True,
        )
    
    async def place_order(
        self,
        account_id: str,
        ticker: str,
        side: OrderSide,
        quantity: Optional[float] = None,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        amount_dollars: Optional[float] = None,
    ) -> TradeOrder:
        """
        Place a simulated order using real Alpha Vantage prices.
        
        Args:
            account_id: Simulated account to trade on
            ticker: Stock ticker
            side: BUY or SELL
            quantity: Number of shares
            order_type: Order type (MARKET, LIMIT, etc.)
            price: Limit price if LIMIT order
            amount_dollars: Dollar amount (auto-calculates quantity)
            
        Returns:
            TradeOrder with execution details
        """
        self._ensure_account_exists(account_id)
        account = self.ledger["accounts"][account_id]
        
        try:
            # Step 1: Get real current price from Alpha Vantage
            logger.info(f"📊 Fetching real price for {ticker}...")
            data = self.financial_tools.get_stock_data(ticker)
            current_price = data.get("price", 0)
            logger.info(f"   Current price: ${current_price:.2f}")
            
            # Step 2: Calculate quantity if amount_dollars provided
            if amount_dollars is not None and quantity is None:
                quantity = amount_dollars / current_price
                logger.info(f"   ${amount_dollars:.2f} ÷ ${current_price:.2f} = {quantity:.4f} shares")
            
            if quantity is None or quantity <= 0:
                raise ValueError("Invalid quantity")
            
            # Step 3: Calculate order value
            if order_type == OrderType.LIMIT and price:
                execution_price = price
            else:
                execution_price = current_price
            
            total_value = execution_price * quantity
            
            # Step 4: Validate account has sufficient funds (BUY only)
            if side == OrderSide.BUY and total_value > account["cash_balance"]:
                error_msg = f"Insufficient funds: ${total_value:.2f} > ${account['cash_balance']:.2f}"
                logger.warning(f"❌ {error_msg}")
                
                return TradeOrder(
                    order_id=f"sim-{uuid.uuid4().hex[:8]}",
                    account_id=account_id,
                    ticker=ticker,
                    side=side,
                    quantity=quantity,
                    status=OrderStatus.REJECTED,
                    error_message=error_msg,
                    simulated=True,
                )
            
            # Step 5: Update simulated portfolio
            positions = account.get("positions", {})

            if side == OrderSide.BUY:
                existing = positions.get(ticker, {"quantity": 0.0, "avg_cost": 0.0})
                # Migrate legacy float entries (pre-DCA tracking)
                if not isinstance(existing, dict):
                    existing = {"quantity": float(existing), "avg_cost": execution_price}
                old_qty = existing["quantity"]
                old_cost = existing["avg_cost"]
                new_qty = old_qty + quantity
                # Weighted average cost
                new_avg = ((old_qty * old_cost) + (quantity * execution_price)) / new_qty
                positions[ticker] = {"quantity": new_qty, "avg_cost": round(new_avg, 4)}
                account["cash_balance"] -= total_value
            else:  # SELL
                existing = positions.get(ticker, {"quantity": 0.0, "avg_cost": 0.0})
                if not isinstance(existing, dict):
                    existing = {"quantity": float(existing), "avg_cost": execution_price}
                current_shares = existing["quantity"]
                if current_shares < quantity:
                    error_msg = f"Cannot sell {quantity} shares of {ticker} (only have {current_shares})"
                    logger.warning(f"❌ {error_msg}")

                    return TradeOrder(
                        order_id=f"sim-{uuid.uuid4().hex[:8]}",
                        account_id=account_id,
                        ticker=ticker,
                        side=side,
                        quantity=quantity,
                        status=OrderStatus.REJECTED,
                        error_message=error_msg,
                        simulated=True,
                    )

                new_qty = current_shares - quantity
                if new_qty <= 0:
                    del positions[ticker]
                else:
                    # avg_cost stays the same on partial sell
                    positions[ticker] = {"quantity": new_qty, "avg_cost": existing["avg_cost"]}
                account["cash_balance"] += total_value
            
            account["positions"] = positions
            self._save_ledger()
            
            # Step 6: Create order record
            order_id = f"sim-{uuid.uuid4().hex[:8]}"
            order = TradeOrder(
                order_id=order_id,
                account_id=account_id,
                ticker=ticker,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                status=OrderStatus.FILLED,
                filled_at=datetime.now(),
                filled_price=execution_price,
                total_value=total_value,
                simulated=True,
            )
            
            # Log the order
            self.ledger["orders"].append({
                "order_id": order_id,
                "account_id": account_id,
                "ticker": ticker,
                "side": side,
                "quantity": quantity,
                "execution_price": execution_price,
                "total_value": total_value,
                "timestamp": datetime.now().isoformat(),
            })
            self._save_ledger()
            
            logger.info(f"✅ Simulated order executed!")
            logger.info(f"   Order ID: {order_id}")
            logger.info(f"   Ticker: {ticker}")
            logger.info(f"   Quantity: {quantity:.4f} shares @ ${execution_price:.2f}")
            logger.info(f"   Total: ${total_value:.2f}")
            logger.info(f"   New cash balance: ${account['cash_balance']:.2f}")
            logger.info(f"   Positions: {list(positions.keys())}")
            
            return order
            
        except Exception as e:
            logger.error(f"❌ Simulated order failed: {e}")
            
            return TradeOrder(
                order_id=f"sim-{uuid.uuid4().hex[:8]}",
                account_id=account_id,
                ticker=ticker,
                side=side,
                quantity=quantity or 0,
                status=OrderStatus.FAILED,
                error_message=str(e),
                simulated=True,
            )
    
    async def close(self):
        """Clean up resources."""
        self._save_ledger()
        logger.info("Mock simulation engine closed")
