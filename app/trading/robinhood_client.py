"""
Robinhood Agentic Trading MCP Client

Integrates with Robinhood's Model Context Protocol (MCP) for agentic trading.
Reference: https://robinhood.com/us/en/support/articles/agentic-trading-overview/

This client provides:
- Account balance and buying power checks
- Order placement (market, limit, stop-loss, etc.)
- Position tracking
- Order history
- Trade execution with human approval gates
"""

import asyncio
import json
import httpx
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field
from loguru import logger

from config.settings import settings
from app.trading.oauth_handler import get_bearer_token
from app.tools.external_tools import FinancialDataTools


class OrderType(str, Enum):
    """Supported order types."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderSide(str, Enum):
    """Order side: buy or sell."""
    BUY = "buy"
    SELL = "sell"


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
    """Trade order representation."""
    
    order_id: str = Field(..., description="Unique order ID")
    account_id: str = Field(..., description="Robinhood account ID")
    ticker: str = Field(..., description="Stock ticker symbol")
    side: OrderSide = Field(..., description="BUY or SELL")
    order_type: OrderType = Field(default=OrderType.MARKET, description="Order type")
    quantity: float = Field(..., description="Number of shares")
    price: Optional[float] = Field(default=None, description="Limit price (if applicable)")
    stop_price: Optional[float] = Field(default=None, description="Stop price (if applicable)")
    status: OrderStatus = Field(default=OrderStatus.PENDING, description="Order status")
    created_at: datetime = Field(default_factory=datetime.now, description="Order creation time")
    filled_at: Optional[datetime] = Field(default=None, description="Order fill time")
    filled_price: Optional[float] = Field(default=None, description="Actual fill price")
    total_value: Optional[float] = Field(default=None, description="Total order value")
    error_message: Optional[str] = Field(default=None, description="Error details if failed")
    
    class Config:
        use_enum_values = True


class AccountInfo(BaseModel):
    """Robinhood account information."""
    
    account_id: str = Field(..., description="Account ID")
    account_number: str = Field(..., description="Full account number")
    account_type: str = Field(..., description="Account type (e.g., 'agentic')")
    buying_power: float = Field(..., description="Available buying power")
    portfolio_value: float = Field(..., description="Total portfolio value")
    cash_balance: float = Field(..., description="Cash on hand")
    positions_count: int = Field(..., description="Number of open positions")
    last_updated: datetime = Field(default_factory=datetime.now, description="Last update time")


class RobinhoodMCPClient:
    """
    MCP Client for Robinhood Agentic Trading.
    
    Handles authentication, order placement, account queries, and trade execution
    through Robinhood's Model Context Protocol endpoint.
    """
    
    def __init__(self):
        """Initialize the Robinhood MCP client with OAuth credentials from settings."""
        self.endpoint = settings.robinhood_mcp_endpoint
        self.account_id = settings.robinhood_account_id
        self.trading_enabled = settings.robinhood_trading_enabled
        
        # Validate configuration
        if not self.account_id:
            logger.warning(
                "Robinhood account ID not configured. "
                "Trading will be disabled. Set ROBINHOOD_ACCOUNT_ID in .env"
            )
        
        self._session: Optional[httpx.AsyncClient] = None
        logger.info(f"✓ Robinhood MCP Client initialized (trading_enabled={self.trading_enabled})")
        logger.info(f"  MCP Endpoint: {self.endpoint}")
        logger.info(f"  Account ID: {self.account_id}")
    
    async def _get_session(self) -> httpx.AsyncClient:
        """Get or create async HTTP session."""
        if self._session is None:
            headers = await self._get_headers()
            self._session = httpx.AsyncClient(
                headers=headers,
                timeout=120.0,  # Increased to 120s for MCP calls
            )
        return self._session
    
    async def _get_headers(self) -> Dict[str, str]:
        """Get request headers for PKCE OAuth authentication."""
        bearer_token = await get_bearer_token()
        
        # Debug: Log the token being used
        logger.info(f"🔑 Bearer token for MCP request:")
        logger.info(f"   Length: {len(bearer_token)} chars")
        logger.info(f"   First 50 chars: {bearer_token[:50]}")
        logger.info(f"   Last 20 chars: ...{bearer_token[-20:]}")
        logger.info(f"   Token type: {type(bearer_token)}")
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer_token}",
        }
        
        logger.info(f"📋 Final headers being sent:")
        logger.info(f"   Content-Type: {headers['Content-Type']}")
        logger.info(f"   Authorization: Bearer {bearer_token[:30]}...{bearer_token[-20:]}")
        
        return headers
    
    async def close(self):
        """Close the HTTP session."""
        if self._session:
            await self._session.aclose()
    
    async def get_account_info(self) -> AccountInfo:
        """
        Fetch account information including balance and buying power.
        
        Returns:
            AccountInfo: Account details
            
        Raises:
            httpx.HTTPError: If API call fails
        """
        if not self.trading_enabled:
            logger.warning("Robinhood trading is disabled")
            raise RuntimeError("Robinhood trading is not enabled. Set ROBINHOOD_TRADING_ENABLED=true")
        
        try:
            session = await self._get_session()
            response = await session.get(
                f"{self.endpoint}/accounts/{self.account_id}",
            )
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"✓ Retrieved account info: {data.get('account_number')}")
            
            return AccountInfo(
                account_id=data.get("id", self.account_id),
                account_number=data.get("account_number", ""),
                account_type=data.get("account_type", "agentic"),
                buying_power=float(data.get("buying_power", 0)),
                portfolio_value=float(data.get("portfolio_value", 0)),
                cash_balance=float(data.get("cash", 0)),
                positions_count=len(data.get("positions", [])),
            )
        except httpx.HTTPError as e:
            logger.error(f"✗ Failed to fetch account info: {str(e)}")
            raise
    
    async def place_order(
        self,
        ticker: str,
        quantity: Optional[float] = None,
        side: OrderSide = OrderSide.BUY,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        amount_dollars: Optional[float] = None,
    ) -> TradeOrder:
        """
        Place a trade order on Robinhood via MCP JSON-RPC 2.0 protocol.
        
        Supports both share-based and dollar-based orders with fractional shares.
        
        Args:
            ticker: Stock ticker symbol (e.g., "NVDA")
            quantity: Number of shares (can be fractional, e.g., 10.5 for 10.5 shares)
                     Ignored if amount_dollars is provided
            side: BUY or SELL
            order_type: Market, Limit, Stop, or Stop-Limit
            price: Limit price (for limit/stop-limit orders)
            stop_price: Stop trigger price (for stop orders)
            amount_dollars: Dollar amount to invest (e.g., 5.00 for $5 worth of shares).
                          If provided, automatically calculates quantity based on current price.
                          Takes precedence over quantity parameter.
            
        Returns:
            TradeOrder: Order details with ID and status
            
        Raises:
            ValueError: If order parameters are invalid
            httpx.HTTPError: If API call fails
            RuntimeError: If Robinhood trading is disabled or OAuth fails
        """
        if not self.trading_enabled:
            logger.warning("Robinhood trading is disabled")
            raise RuntimeError("Robinhood trading is not enabled")
        
        # Determine quantity: use amount_dollars if provided, otherwise use quantity parameter
        if amount_dollars is not None:
            if amount_dollars <= 0:
                raise ValueError(f"Dollar amount must be positive, got ${amount_dollars}")
            
            try:
                logger.info(f"Fetching current price for {ticker}...")
                financial_tools = FinancialDataTools()
                stock_data = financial_tools.get_stock_data(ticker)
                current_price = stock_data.get("price")
                
                if current_price is None or current_price <= 0:
                    raise ValueError(f"Could not fetch valid price for {ticker}")
                
                # Calculate quantity from dollar amount
                quantity = amount_dollars / current_price
                logger.info(
                    f"✓ Converting ${amount_dollars:.2f} to {quantity:.4f} shares "
                    f"of {ticker} @ ${current_price:.2f}/share"
                )
            except Exception as e:
                logger.error(f"✗ Failed to convert dollars to shares: {str(e)}")
                raise ValueError(
                    f"Cannot execute dollar-based order: {str(e)}. "
                    f"Try specifying quantity instead."
                )
        
        elif quantity is None:
            raise ValueError(
                "Either 'quantity' (number of shares) or 'amount_dollars' "
                "(dollar amount) must be provided"
            )
        
        # Validate share quantity
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")
        
        if order_type == OrderType.LIMIT and price is None:
            raise ValueError("Limit orders require a price")
        
        if order_type == OrderType.STOP and stop_price is None:
            raise ValueError("Stop orders require a stop_price")
        
        try:
            # Get valid OAuth bearer token
            bearer_token = await get_bearer_token()
            
            # Build MCP JSON-RPC 2.0 request payload
            # See: https://robinhood.com/us/en/support/articles/agentic-trading-overview/
            order_arguments = {
                "account_id": self.account_id,
                "ticker": ticker.upper(),
                "side": side.value.lower(),  # "buy" or "sell"
                "quantity": quantity,  # Supports fractional shares (e.g., 10.5)
                "time_in_force": "day",  # day, gtc (good-til-canceled)
            }
            
            # Add optional price parameters
            if order_type == OrderType.LIMIT and price:
                order_arguments["price"] = float(price)
            elif order_type == OrderType.STOP and stop_price:
                order_arguments["stop_price"] = float(stop_price)
            elif order_type == OrderType.STOP_LIMIT and price and stop_price:
                order_arguments["price"] = float(price)
                order_arguments["stop_price"] = float(stop_price)
            
            # JSON-RPC 2.0 wrapper for MCP tools/call
            rpc_payload = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "place_order",
                    "arguments": order_arguments,
                },
            }
            
            logger.debug(f"MCP RPC Payload: {json.dumps(rpc_payload, indent=2)}")
            
            # Make MCP API call
            headers = await self._get_headers()
            
            logger.info(f"📤 Sending MCP API request:")
            logger.info(f"   URL: {self.endpoint}")
            logger.info(f"   Method: POST")
            logger.info(f"   Headers: {headers}")
            logger.info(f"   Payload: {json.dumps(rpc_payload, indent=2)}")
            
            async with httpx.AsyncClient() as client:
                logger.info(f"🚀 Making httpx POST request...")
                response = await client.post(
                    self.endpoint,
                    json=rpc_payload,
                    headers=headers,
                    timeout=120.0,  # Increased to 120s for MCP calls
                )
                
                logger.info(f"📥 Response received:")
                logger.info(f"   Status: {response.status_code}")
                logger.info(f"   Headers: {dict(response.headers)}")
                logger.info(f"   Body: {response.text[:500]}")
                
                response.raise_for_status()
            
            # Parse JSON-RPC response
            response_data = response.json()
            logger.debug(f"MCP Response: {json.dumps(response_data, indent=2)}")
            
            # Extract result from JSON-RPC envelope
            if "error" in response_data:
                error_msg = response_data["error"].get("message", "Unknown MCP error")
                logger.error(f"✗ MCP Error: {error_msg}")
                raise RuntimeError(f"MCP trade execution failed: {error_msg}")
            
            result = response_data.get("result", {})
            order_id = result.get("order_id") or result.get("id", "")
            order_status = result.get("status", "submitted")
            filled_price = result.get("filled_price") or result.get("price")
            
            order = TradeOrder(
                order_id=order_id,
                account_id=self.account_id,
                ticker=ticker.upper(),
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                status=OrderStatus(order_status),
                filled_price=filled_price,
                created_at=datetime.now(),
            )
            
            logger.info(
                f"✓ Order placed via MCP: {side.value.upper()} {quantity} {ticker.upper()} "
                f"@ {order_type.value} | Order ID: {order.order_id} | Status: {order_status}"
            )
            return order
            
        except RuntimeError as e:
            logger.error(f"✗ OAuth or validation error: {str(e)}")
            raise
        except httpx.HTTPError as e:
            logger.error(f"✗ MCP API call failed: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"✗ Unexpected error during order placement: {str(e)}")
            raise
    
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.
        
        Args:
            order_id: ID of the order to cancel
            
        Returns:
            bool: True if cancellation successful
        """
        if not self.trading_enabled:
            raise RuntimeError("Robinhood trading is not enabled")
        
        try:
            session = await self._get_session()
            response = await session.delete(
                f"{self.endpoint}/orders/{order_id}",
            )
            response.raise_for_status()
            logger.info(f"✓ Order {order_id} cancelled")
            return True
        except httpx.HTTPError as e:
            logger.error(f"✗ Failed to cancel order: {str(e)}")
            raise
    
    async def get_order_status(self, order_id: str) -> TradeOrder:
        """
        Get status of an order.
        
        Args:
            order_id: ID of the order
            
        Returns:
            TradeOrder: Current order details
        """
        try:
            session = await self._get_session()
            response = await session.get(
                f"{self.endpoint}/orders/{order_id}",
            )
            response.raise_for_status()
            
            data = response.json()
            order = TradeOrder(
                order_id=order_id,
                account_id=data.get("account_id", self.account_id),
                ticker=data.get("instrument", ""),
                side=OrderSide(data.get("side", "buy")),
                order_type=OrderType(data.get("type", "market")),
                quantity=float(data.get("quantity", 0)),
                price=data.get("price"),
                stop_price=data.get("stop_price"),
                status=OrderStatus(data.get("status", "pending")),
                filled_price=data.get("filled_price"),
            )
            return order
        except httpx.HTTPError as e:
            logger.error(f"✗ Failed to get order status: {str(e)}")
            raise
    
    async def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get all open positions in the account.
        
        Returns:
            List of position dictionaries with ticker, quantity, avg_cost, etc.
        """
        if not self.trading_enabled:
            raise RuntimeError("Robinhood trading is not enabled")
        
        try:
            session = await self._get_session()
            response = await session.get(
                f"{self.endpoint}/accounts/{self.account_id}/positions",
            )
            response.raise_for_status()
            
            positions = response.json().get("positions", [])
            logger.info(f"✓ Retrieved {len(positions)} positions")
            return positions
        except httpx.HTTPError as e:
            logger.error(f"✗ Failed to get positions: {str(e)}")
            raise
    
    async def validate_order(
        self,
        ticker: str,
        quantity: float,
        side: OrderSide,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Validate an order before placement (check balance, buying power, etc).
        
        Args:
            ticker: Stock ticker symbol
            quantity: Number of shares
            side: BUY or SELL
            price: Price per share (for buying power calculation)
            
        Returns:
            dict: Validation results with "valid" bool and any warnings/errors
        """
        try:
            account = await self.get_account_info()
            
            # Estimate order value
            if side == OrderSide.BUY:
                order_value = quantity * (price or 100)  # Assume ~$100 if no price
                
                if order_value > account.buying_power:
                    return {
                        "valid": False,
                        "error": f"Insufficient buying power: ${order_value:.2f} needed, "
                                f"${account.buying_power:.2f} available",
                        "buying_power": account.buying_power,
                        "required": order_value,
                    }
            
            return {
                "valid": True,
                "account_balance": account.portfolio_value,
                "buying_power": account.buying_power,
                "cash_balance": account.cash_balance,
            }
        except Exception as e:
            logger.error(f"✗ Order validation failed: {str(e)}")
            return {
                "valid": False,
                "error": str(e),
            }


# Singleton instance
_robinhood_client: Optional[RobinhoodMCPClient] = None


def get_robinhood_client() -> RobinhoodMCPClient:
    """Get or create the Robinhood MCP client singleton."""
    global _robinhood_client
    if _robinhood_client is None:
        _robinhood_client = RobinhoodMCPClient()
    return _robinhood_client


async def close_robinhood_client():
    """Close the Robinhood MCP client."""
    global _robinhood_client
    if _robinhood_client:
        await _robinhood_client.close()
        _robinhood_client = None
