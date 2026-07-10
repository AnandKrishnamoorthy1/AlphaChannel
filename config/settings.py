"""
Configuration management for Corporate Intelligence Engine.

Loads environment variables and provides typed configuration objects
for the backend, frontend, and orchestrator.

Usage:
    from config import settings
    
    print(settings.backend_host)
    print(settings.openai_api_key)
"""

import os
from pathlib import Path
from functools import lru_cache

# Load .env file at module import time
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=True)
from typing import Optional, List
from pydantic_settings import BaseSettings
from pydantic import Field, validator


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    Environment variables are read from:
    1. .env file (highest priority)
    2. System environment variables
    3. Default values defined here
    """
    
    # ========================================================================
    # API CONFIGURATION
    # ========================================================================
    
    backend_host: str = Field(default="0.0.0.0", env="BACKEND_HOST")
    backend_port: int = Field(default=9000, env="BACKEND_PORT")  # Changed from 8000 to 9000 (Alibaba Cloud FC requirement)
    backend_reload: bool = Field(default=True, env="BACKEND_RELOAD")
    backend_log_level: str = Field(default="info", env="BACKEND_LOG_LEVEL")
    
    frontend_host: str = Field(default="localhost", env="FRONTEND_HOST")
    frontend_port: int = Field(default=8501, env="FRONTEND_PORT")
    
    api_request_timeout: int = Field(default=60, env="API_REQUEST_TIMEOUT")
    backend_api_url: Optional[str] = Field(default="http://localhost:8002", env="BACKEND_API_URL")
    
    # ========================================================================
    # LLM CONFIGURATION
    # ========================================================================
    
    openai_api_key: Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4-turbo", env="OPENAI_MODEL")
    
    anthropic_api_key: Optional[str] = Field(default=None, env="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-3-opus-20240229", env="ANTHROPIC_MODEL")
    
    gemini_api_key: Optional[str] = Field(default=None, env="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-1.5-pro", env="GEMINI_MODEL")
    
    dashscope_api_key: Optional[str] = Field(default=None, env="DASHSCOPE_API_KEY")
    qwen_api_key: Optional[str] = Field(default=None, env="DASHSCOPE_API_KEY")  # Alias for backward compatibility
    qwen_model: str = Field(default="qwen3.7-plus", env="QWEN_MODEL")
    qwen_routing_model: str = Field(default="qwen3.6-flash", env="QWEN_ROUTING_MODEL")
    qwen_temperature: float = Field(default=0.3, env="QWEN_TEMPERATURE")
    qwen_top_p: float = Field(default=0.65, env="QWEN_TOP_P")
    dashscope_endpoint: Optional[str] = Field(
        default="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        env="DASHSCOPE_ENDPOINT",
        description="DashScope endpoint URL - uses -intl subdomain for international access"
    )
    
    # ========================================================================
    # FINANCIAL DATA APIS
    # ========================================================================
    
    alpha_vantage_api_key: Optional[str] = Field(default=None, env="ALPHA_VANTAGE_API_KEY")
    # Optional second key for OVERVIEW endpoint — doubles daily quota and
    # eliminates rate-limit collisions when both calls run in parallel.
    # Falls back to alpha_vantage_api_key if not set.
    alpha_vantage_api_key_2: Optional[str] = Field(default=None, env="ALPHA_VANTAGE_API_KEY_2")
    finnhub_api_key: Optional[str] = Field(default=None, env="FINNHUB_API_KEY")
    
    # ========================================================================
    # ROBINHOOD AGENTIC TRADING CONFIGURATION (OAuth 2.1 PKCE - No Client Secret!)
    # ========================================================================
    
    robinhood_mcp_endpoint: str = Field(
        default="https://agent.robinhood.com/mcp/trading",
        env="ROBINHOOD_MCP_ENDPOINT",
        description="Robinhood Agentic Trading MCP endpoint"
    )
    robinhood_account_id: Optional[str] = Field(
        default=None,
        env="ROBINHOOD_ACCOUNT_ID",
        description="Your Robinhood account ID for agentic trading"
    )
    robinhood_trading_enabled: bool = Field(
        default=False,
        env="ROBINHOOD_TRADING_ENABLED",
        description="Enable agentic trading (PKCE OAuth - no secrets needed)"
    )
    
    # ========================================================================
    # BROKER SELECTION & MULTI-TENANCY CONFIGURATION
    # ========================================================================
    
    # IMPORTANT: For production/public deployment, set BROKER_TYPE=simulation
    # This prevents accidental real money execution and enables multi-user demo
    broker_type: str = Field(
        default="live",
        env="BROKER_TYPE",
        description="Broker implementation: 'live' for real Robinhood trading, 'simulation' for paper trading"
    )
    
    # Session-based simulation account for public users
    use_session_simulation_account: bool = Field(
        default=False,
        env="USE_SESSION_SIMULATION_ACCOUNT",
        description="Generate per-session simulation account IDs (for multi-user public app)"
    )
    
    # Simulation ledger path (where paper trading state is stored)
    simulation_ledger_path: str = Field(
        default="simulation_ledger.json",
        env="SIMULATION_LEDGER_PATH",
        description="Path to JSON file storing paper trading state"
    )
    
    # ========================================================================
    # DATABASE CONFIGURATION
    # ========================================================================
    
    database_url: Optional[str] = Field(default=None, env="DATABASE_URL")
    database_echo: bool = Field(default=False, env="DATABASE_ECHO")
    redis_url: Optional[str] = Field(default=None, env="REDIS_URL")
    
    # ========================================================================
    # MONITORING & OBSERVABILITY
    # ========================================================================
    
    langsmith_api_key: Optional[str] = Field(default=None, env="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="corporate-intelligence-engine", env="LANGSMITH_PROJECT")
    sentry_dsn: Optional[str] = Field(default=None, env="SENTRY_DSN")
    
    # ========================================================================
    # AWS CONFIGURATION
    # ========================================================================
    
    aws_region: str = Field(default="us-east-1", env="AWS_REGION")
    aws_access_key_id: Optional[str] = Field(default=None, env="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[str] = Field(default=None, env="AWS_SECRET_ACCESS_KEY")
    s3_bucket_name: Optional[str] = Field(default="corporate-intelligence-reports", env="S3_BUCKET_NAME")
    
    # ========================================================================
    # DEVELOPMENT SETTINGS
    # ========================================================================
    
    debug: bool = Field(default=False, env="DEBUG")
    environment: str = Field(default="development", env="ENVIRONMENT")
    cors_origins: str = Field(
        default="http://localhost:8501,http://localhost:3000",
        env="CORS_ORIGINS"
    )
    
    # ========================================================================
    # APPLICATION SETTINGS
    # ========================================================================
    
    agent_max_iterations: int = Field(default=10, env="AGENT_MAX_ITERATIONS")
    agent_timeout: int = Field(default=30, env="AGENT_TIMEOUT")
    report_format: str = Field(default="markdown", env="REPORT_FORMAT")
    report_include_charts: bool = Field(default=True, env="REPORT_INCLUDE_CHARTS")
    
    # ========================================================================
    # LOGGING
    # ========================================================================
    
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_file_path: str = Field(default="logs/app.log", env="LOG_FILE_PATH")
    
    # ========================================================================
    # SECURITY
    # ========================================================================
    
    secret_key: str = Field(default="dev-secret-key-change-in-production", env="SECRET_KEY")
    secure: bool = Field(default=False, env="SECURE")
    ssl_cert_file: Optional[str] = Field(default=None, env="SSL_CERT_FILE")
    ssl_key_file: Optional[str] = Field(default=None, env="SSL_KEY_FILE")
    
    # ========================================================================
    # PYDANTIC CONFIGURATION
    # ========================================================================
    
    class Config:
        """Pydantic configuration."""
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
    
    # ========================================================================
    # PROPERTIES
    # ========================================================================
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins as list."""
        return [origin.strip() for origin in self.cors_origins.split(",")]
    
    @property
    def backend_url(self) -> str:
        """Full backend URL."""
        protocol = "https" if self.secure else "http"
        return f"{protocol}://{self.backend_host}:{self.backend_port}"
    
    @property
    def frontend_url(self) -> str:
        """Full frontend URL."""
        return f"http://{self.frontend_host}:{self.frontend_port}"
    
    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.environment.lower() == "production"
    
    @property
    def is_development(self) -> bool:
        """Check if running in development."""
        return self.environment.lower() == "development"
    
    @property
    def has_openai(self) -> bool:
        """Check if OpenAI is configured."""
        return bool(self.openai_api_key)
    
    @property
    def has_anthropic(self) -> bool:
        """Check if Anthropic is configured."""
        return bool(self.anthropic_api_key)
    
    @property
    def has_gemini(self) -> bool:
        """Check if Google Gemini is configured."""
        return bool(self.gemini_api_key)
    
    @property
    def has_database(self) -> bool:
        """Check if database is configured."""
        return bool(self.database_url)
    
    @property
    def has_redis(self) -> bool:
        """Check if Redis is configured."""
        return bool(self.redis_url)


@lru_cache()
def get_settings() -> Settings:
    """
    Get singleton settings instance.
    
    Caches the Settings object so it's only created once.
    
    Returns:
        Settings: Application configuration
    """
    return Settings()


# Global settings instance
settings = get_settings()


if __name__ == "__main__":
    """Display current configuration (masks sensitive values)."""
    import json
    
    config_dict = settings.model_dump()
    
    # Mask sensitive keys
    sensitive_keys = [
        "openai_api_key", "anthropic_api_key", "secret_key",
        "aws_access_key_id", "aws_secret_access_key",
        "langsmith_api_key", "sentry_dsn", "database_url", "redis_url"
    ]
    
    for key in sensitive_keys:
        if config_dict.get(key):
            config_dict[key] = "***MASKED***"
    
    print("\n" + "=" * 80)
    print("APPLICATION CONFIGURATION")
    print("=" * 80)
    print(json.dumps(config_dict, indent=2, default=str))
    print("=" * 80 + "\n")
