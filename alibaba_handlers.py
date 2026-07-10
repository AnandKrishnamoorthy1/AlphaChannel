"""
Alibaba Cloud Function Compute WSGI Handlers

This module provides WSGI wrappers for FastAPI and Streamlit applications
to run on Alibaba Cloud Function Compute (FC).

FC expects a handler function signature:
    def handler(environ, start_response):
        return app(environ, start_response)

FastAPI and Streamlit are ASGI applications, so we wrap them with
starlette.middleware.wsgi.WSGIMiddleware to convert ASGI→WSGI.
"""

import os
import sys
from typing import Callable, Tuple

# ============================================================================
# FastAPI WSGI Handler (for backend.py)
# ============================================================================

def create_fastapi_handler() -> Callable:
    """
    Create a WSGI handler for the FastAPI backend.
    
    Usage in backend.py:
        from alibaba_handlers import create_fastapi_handler
        handler = create_fastapi_handler()
    
    Then deploy with s.yaml:
        handler: backend.handler
    """
    from starlette.middleware.wsgi import WSGIMiddleware
    from backend import app as fastapi_app
    
    # Wrap FastAPI (ASGI) app with WSGIMiddleware to make it WSGI-compatible
    wsgi_app = WSGIMiddleware(fastapi_app)
    
    def handler(environ: dict, start_response: Callable) -> list:
        """
        WSGI handler for Alibaba Cloud Function Compute.
        
        Args:
            environ: WSGI environment dictionary
            start_response: Callable to start HTTP response
            
        Returns:
            Response body as list of bytes
        """
        # Ensure database connection is disposed after request
        try:
            return wsgi_app(environ, start_response)
        finally:
            # Clean up database connections for serverless execution
            try:
                from app.tools.database import engine
                if engine:
                    engine.dispose()
            except Exception:
                pass  # Ignore cleanup errors
    
    return handler


# ============================================================================
# Streamlit WSGI Handler (for frontend.py)
# ============================================================================

def create_streamlit_handler() -> Callable:
    """
    Create a WSGI handler for the Streamlit frontend.
    
    Note: Streamlit in serverless is not recommended due to session state management.
    Consider using a separate REST API + React frontend instead.
    
    Usage:
        from alibaba_handlers import create_streamlit_handler
        handler = create_streamlit_handler()
    """
    from starlette.middleware.wsgi import WSGIMiddleware
    import streamlit.web.bootstrap as bootstrap
    
    def handler(environ: dict, start_response: Callable) -> list:
        """WSGI handler for Streamlit on Alibaba Cloud FC."""
        # Streamlit is complex for serverless. Return a simple redirect to frontend service.
        status = '307 Temporary Redirect'
        headers = [
            ('Location', f"https://{environ.get('HTTP_HOST', 'localhost')}/"),
            ('Content-Type', 'text/plain')
        ]
        start_response(status, headers)
        return [b'Streamlit frontend is deployed separately']
    
    return handler


# ============================================================================
# FastAPI App Module Handler (Direct export from backend.py)
# ============================================================================

# This is the simplest approach: directly export the WSGI-wrapped app from backend.py
# 
# In backend.py, add this at the bottom:
#
#   from starlette.middleware.wsgi import WSGIMiddleware
#   if os.getenv("ENVIRONMENT") == "production":
#       from alibaba_handlers import wrap_for_fc
#       handler = wrap_for_fc(app)
#   else:
#       handler = None  # Use uvicorn locally

def wrap_for_fc(asgi_app) -> Callable:
    """
    Wrap an ASGI application (FastAPI, Starlette) for Alibaba Cloud FC.
    
    Usage in backend.py:
        from starlette.middleware.wsgi import WSGIMiddleware
        from alibaba_handlers import wrap_for_fc
        
        app = FastAPI(...)
        
        if os.getenv("ENVIRONMENT") == "production":
            handler = wrap_for_fc(app)
    
    Args:
        asgi_app: FastAPI or Starlette ASGI application
        
    Returns:
        WSGI handler function compatible with Alibaba Cloud FC
    """
    from starlette.middleware.wsgi import WSGIMiddleware
    
    wsgi_app = WSGIMiddleware(asgi_app)
    
    def handler(environ: dict, start_response: Callable) -> list:
        """
        WSGI handler for Alibaba Cloud Function Compute.
        
        Handles:
        - Converting WSGI environ to Starlette request
        - Executing FastAPI route handlers
        - Streaming responses with proper headers
        - Database connection cleanup after each request
        """
        try:
            # Execute the ASGI app via WSGI bridge
            response_iterator = wsgi_app(environ, start_response)
            
            # Ensure response is fully consumed
            return list(response_iterator)
            
        except Exception as e:
            # Log error and return 500 response
            import traceback
            from loguru import logger
            
            logger.error(f"FC handler error: {e}\n{traceback.format_exc()}")
            
            status = '500 Internal Server Error'
            headers = [('Content-Type', 'application/json')]
            start_response(status, headers)
            
            return [b'{"error": "Internal server error"}']
            
        finally:
            # CRITICAL: Clean up database connections in serverless
            try:
                from app.tools.database import engine
                if engine:
                    engine.dispose()
            except Exception:
                pass
    
    return handler


# ============================================================================
# Health Check Handler (for FC readiness probes)
# ============================================================================

def health_handler(environ: dict, start_response: Callable) -> list:
    """
    Simple health check endpoint for Alibaba Cloud FC.
    
    Usage:
        s.yaml:
          triggers:
            - type: http
              properties:
                paths:
                  - /health
    
    Returns:
        200 OK with JSON status
    """
    import json
    
    status = '200 OK'
    headers = [('Content-Type', 'application/json')]
    start_response(status, headers)
    
    response = {
        "status": "healthy",
        "environment": os.getenv("ENVIRONMENT", "unknown"),
        "service": "corporate-intelligence-engine"
    }
    
    return [json.dumps(response).encode('utf-8')]


# ============================================================================
# Export for use in backend.py
# ============================================================================

__all__ = [
    'create_fastapi_handler',
    'create_streamlit_handler',
    'wrap_for_fc',
    'health_handler',
]
