"""
Trading module - handles Robinhood agentic trading integration.

Provides MCP client for executing trades via Robinhood's agentic trading API.
Also includes broker abstraction for supporting both live and simulated trading.
"""

from .robinhood_client import (
    RobinhoodMCPClient,
    TradeOrder,
    AccountInfo,
    OrderStatus,
    OrderSide,
    OrderType,
)
from .broker_interface import (
    BaseBroker,
)
from .mock_simulation_engine import (
    MockSimulationEngine,
)
from .broker_factory import (
    get_broker_for_user,
    get_live_broker,
    get_simulation_broker,
    get_account_id_for_user,
)
from app.trading.oauth_handler import (
    OAuthHandler,
    TokenStore,
    get_oauth_handler,
    get_bearer_token,
)

__all__ = [
    # Live trading
    "RobinhoodMCPClient",
    # Common models
    "TradeOrder",
    "AccountInfo",
    "OrderStatus",
    "OrderSide",
    "OrderType",
    # Broker abstraction
    "BaseBroker",
    "MockSimulationEngine",
    # Broker factory (dependency injection)
    "get_broker_for_user",
    "get_live_broker",
    "get_simulation_broker",
    "get_account_id_for_user",
    # OAuth
    "OAuthHandler",
    "TokenStore",
    "get_oauth_handler",
    "get_bearer_token",
]
