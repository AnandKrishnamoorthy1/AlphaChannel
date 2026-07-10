"""
Broker Factory - Dependency Injection for Trading Clients

Seamlessly routes between live Robinhood trading and paper trading simulation
based on environment configuration and user session state.

This is the GATEWAY that enforces security boundaries for public deployments.
"""

import os
from typing import Dict, Optional
from pathlib import Path
from loguru import logger

from config.settings import settings
from app.trading.broker_interface import BaseBroker


def get_broker_for_user(user_session: Optional[Dict] = None) -> BaseBroker:
    """
    Get the appropriate broker client for a user.
    
    SECURITY MODEL:
    - Admin users (ROBINHOOD_TRADING_ENABLED=true) → Live Robinhood client
    - Everyone else → Simulation engine (paper trading only)
    
    This is the CRITICAL GUARD that prevents public users from hitting live accounts.
    
    Args:
        user_session: Optional session dict with user info (e.g., {"is_admin": True})
        
    Returns:
        BaseBroker implementation (either RobinhoodMCPClient or MockSimulationEngine)
        
    Examples:
        # For admin/owner (live trading)
        if os.getenv("ROBINHOOD_TRADING_ENABLED") == "true":
            broker = get_broker_for_user({"is_admin": True})  # → Live Robinhood
        
        # For public demo user (paper trading)
        broker = get_broker_for_user()  # → Simulation engine
    """
    
    # Check if broker_type is explicitly set to simulation
    if settings.broker_type.lower() == "simulation":
        logger.info("🎮 SIMULATION MODE: Using mock trading engine (paper trading)")
        return get_simulation_broker()
    
    # Check if live trading is enabled
    if not settings.robinhood_trading_enabled:
        logger.warning("⚠️  Live trading disabled. Falling back to simulation engine.")
        return get_simulation_broker()
    
    # For admin users with live trading enabled
    if user_session and user_session.get("is_admin"):
        logger.info("🚀 LIVE MODE: Using Robinhood MCP client (real money!)")
        return get_live_broker()
    
    # Default: everyone else gets simulation
    logger.info("🎮 SAFE MODE: Public/demo user. Using simulation engine (paper trading)")
    return get_simulation_broker()


def get_live_broker() -> BaseBroker:
    """
    Get the live Robinhood MCP trading client.
    
    ⚠️  WARNING: This connects to real money trading
    Only use this if:
    1. ROBINHOOD_TRADING_ENABLED=true in .env
    2. User is authenticated as admin/owner
    3. Valid Robinhood account ID configured
    
    Returns:
        RobinhoodMCPClient instance
    """
    from app.trading.robinhood_client import RobinhoodMCPClient
    
    logger.warning("🔴 INITIALIZING LIVE ROBINHOOD CLIENT!")
    logger.warning("   Account ID: {}".format(settings.robinhood_account_id))
    logger.warning("   Endpoint: {}".format(settings.robinhood_mcp_endpoint))
    logger.warning("   ⚠️  REAL MONEY TRADING ENABLED - USE WITH CAUTION!")
    
    return RobinhoodMCPClient()


def get_simulation_broker() -> BaseBroker:
    """
    Get the mock simulation (paper trading) engine.
    
    Safe for:
    - Public GitHub repositories
    - Multi-user SaaS deployments
    - Testing without financial risk
    - Teaching/demos
    
    Uses REAL prices from Alpha Vantage but simulated portfolio state.
    
    Returns:
        MockSimulationEngine instance
    """
    from app.trading.mock_simulation_engine import MockSimulationEngine
    
    ledger_path = Path(settings.simulation_ledger_path)
    logger.info(f"🎮 Initializing simulation engine")
    logger.info(f"   Ledger: {ledger_path}")
    logger.info(f"   Starting balance: $10,000")
    logger.info(f"   Real prices: Alpha Vantage")
    
    return MockSimulationEngine(db_path=ledger_path)


def get_account_id_for_user(user_session: Optional[Dict] = None) -> str:
    """
    Get the appropriate account ID for a user.
    
    For live trading: Returns the real Robinhood account ID
    For simulation: Returns a session-based sim account (if enabled) or default
    
    Args:
        user_session: Optional session dict (may contain session_id, user_id, etc.)
        
    Returns:
        Account ID string
    """
    
    # If live trading and user is admin
    if settings.robinhood_trading_enabled and user_session and user_session.get("is_admin"):
        return settings.robinhood_account_id or "unknown"
    
    # For simulation users, generate session-specific account if enabled
    if settings.use_session_simulation_account and user_session:
        session_id = user_session.get("session_id") or user_session.get("user_id")
        if session_id:
            sim_account = f"SIM-{session_id[:12]}"
            logger.debug(f"Generated session-specific sim account: {sim_account}")
            return sim_account
    
    # Default simulation account
    return "SIM-DEFAULT"


# Export convenience functions
__all__ = [
    "get_broker_for_user",
    "get_live_broker",
    "get_simulation_broker",
    "get_account_id_for_user",
]
