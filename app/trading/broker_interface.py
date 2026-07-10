"""
Abstract Broker Interface for Multi-Tenancy Support

Defines a strict contract for both live trading clients and simulation engines.
This enables seamless switching between:
- Live Robinhood trading (admin/owner)
- Paper trading simulation (public demo users)

Architecture Pattern: Gateway Pattern with Dependency Injection
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field


class OrderSide(str, Enum):
    """Order side: buy or sell."""
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """Supported order types."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    """Order execution status."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"


class TradeOrder(BaseModel):
    """Unified trade order representation."""
    
    order_id: str = Field(..., description="Unique order ID")
    account_id: str = Field(..., description="Account ID")
    ticker: str = Field(..., description="Stock ticker symbol")
    side: OrderSide = Field(..., description="BUY or SELL")
    order_type: OrderType = Field(default=OrderType.MARKET, description="Order type")
    quantity: float = Field(..., description="Number of shares")
    price: Optional[float] = Field(default=None, description="Limit price")
    status: OrderStatus = Field(default=OrderStatus.PENDING, description="Order status")
    created_at: datetime = Field(default_factory=datetime.now, description="Order creation time")
    filled_at: Optional[datetime] = Field(default=None, description="Order fill time")
    filled_price: Optional[float] = Field(default=None, description="Actual fill price")
    total_value: Optional[float] = Field(default=None, description="Total order value")
    error_message: Optional[str] = Field(default=None, description="Error details if failed")
    simulated: bool = Field(default=False, description="Whether this is a simulated trade")
    
    class Config:
        use_enum_values = True


class AccountInfo(BaseModel):
    """Unified account information representation."""
    
    account_id: str = Field(..., description="Account ID")
    account_type: str = Field(..., description="Account type")
    buying_power: float = Field(..., description="Available buying power")
    portfolio_value: float = Field(..., description="Total portfolio value")
    cash_balance: float = Field(..., description="Cash on hand")
    positions_count: int = Field(..., description="Number of open positions")
    last_updated: datetime = Field(default_factory=datetime.now, description="Last update time")
    is_simulated: bool = Field(default=False, description="Whether this is simulated account")


class BaseBroker(ABC):
    """
    Abstract Broker Interface
    
    All broker implementations (live Robinhood, mock simulation) must implement these methods.
    This ensures the agent can seamlessly switch between real and simulated trading.
    """
    
    @abstractmethod
    async def get_account_info(self, account_id: str) -> AccountInfo:
        """
        Fetch account information including balance and buying power.
        
        Args:
            account_id: The account ID to query
            
        Returns:
            AccountInfo: Account details
            
        Raises:
            Exception: If account lookup fails
        """
        pass
    
    @abstractmethod
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
        Place a trade order.
        
        Args:
            account_id: Account to trade on
            ticker: Stock ticker (e.g., "AMD", "NVDA")
            side: BUY or SELL
            quantity: Number of shares (mutually exclusive with amount_dollars)
            order_type: Order type (MARKET, LIMIT, STOP, etc.)
            price: Limit price (required if order_type is LIMIT)
            amount_dollars: Dollar amount (auto-calculates quantity)
            
        Returns:
            TradeOrder: Order details including execution price and status
            
        Raises:
            ValueError: If parameters are invalid
            Exception: If order placement fails
        """
        pass
    
    @abstractmethod
    async def close(self):
        """Clean up broker resources (HTTP sessions, etc.)."""
        pass
