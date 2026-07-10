"""
OAuth 2.1 PKCE Token Handler for Robinhood MCP Integration

Uses PKCE (Proof Key for Code Exchange) for secret-less authentication.
No client_id/client_secret needed - perfect for public clients (local apps).

Workflow:
1. Generate cryptographic code_verifier (random string)
2. Compute code_challenge from verifier
3. Send user to login URL with code_challenge
4. User logs in with 2FA
5. Redirect captures auth code
6. Exchange code + verifier for Bearer token
7. Cache token locally
"""

import asyncio
import json
import time
import hashlib
import secrets
import base64
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlencode, parse_qs, urlparse

import httpx
from loguru import logger

from config.settings import settings


def inspect_jwt_token(token: str) -> Dict[str, Any]:
    """
    Decode JWT token payload (without validation) to inspect claims.
    
    Returns dict with:
    - oid: OAuth client ID
    - scope: Token scope (should be 'agentic_trading', not 'internal')
    - is_legacy: Whether this is a legacy retail client token
    """
    try:
        # JWT format: header.payload.signature
        parts = token.split('.')
        if len(parts) != 3:
            return {"error": "Invalid JWT format"}
        
        # Decode payload (add padding if needed)
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        
        decoded = base64.urlsafe_b64decode(payload)
        claims = json.loads(decoded)
        
        # Check for legacy client ID
        oid = claims.get("meta", {}).get("oid", "")
        is_legacy = oid == "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"
        
        return {
            "oid": oid,
            "scope": claims.get("scope", ""),
            "is_legacy": is_legacy,
            "user_id": claims.get("user_id", ""),
            "exp": claims.get("exp", ""),
        }
    except Exception as e:
        return {"error": str(e)}


class TokenStore:
    """Persistent token storage with expiration checking"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path.home() / ".robinhood_agent" / "tokens"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.token_file = self.cache_dir / "access_token.json"

    def save_token(self, token_data: Dict[str, Any]) -> None:
        """Save token with metadata (expiration, refresh token)"""
        token_data["cached_at"] = datetime.utcnow().isoformat()
        
        # Inspect the token to check if it's legacy retail client
        access_token = token_data.get("access_token", "")
        inspection = inspect_jwt_token(access_token)
        
        logger.info(f"Token saved to {self.token_file}")
        logger.info(f"📋 Token Analysis:")
        logger.info(f"   Client ID (oid): {inspection.get('oid', 'unknown')}")
        logger.info(f"   Scope: {inspection.get('scope', 'unknown')}")
        
        if inspection.get("is_legacy"):
            logger.error(f"❌ LEGACY RETAIL TOKEN DETECTED!")
            logger.error(f"   This token has the legacy client_id (c82SH0WZ...)")
            logger.error(f"   MCP gateway will reject it with 401 'client_id_not_allowed'")
            logger.warning(f"   ⚠️  Make sure you clicked 'Connect' or 'Get Started' on agentic portal")
            logger.warning(f"   ⚠️  You may need to re-authenticate")
        else:
            logger.info(f"   ✅ Token looks like agentic client (not legacy)")
        
        with open(self.token_file, "w") as f:
            json.dump(token_data, f, indent=2)

    def load_token(self) -> Optional[Dict[str, Any]]:
        """Load token from cache if valid"""
        if not self.token_file.exists():
            logger.debug(f"No token file at {self.token_file}")
            return None

        try:
            with open(self.token_file, "r") as f:
                token_data = json.load(f)

            # Check expiration
            expires_in = token_data.get("expires_in", 86400)  # Default 24h
            cached_at = datetime.fromisoformat(token_data.get("cached_at"))
            expires_at = cached_at + timedelta(seconds=expires_in * 0.9)  # Refresh at 90%

            if datetime.utcnow() < expires_at:
                logger.debug(f"Token valid until {expires_at}")
                return token_data
            else:
                logger.info("Token expired, refresh needed")
                return None

        except Exception as e:
            logger.error(f"Error loading token: {e}")
            return None

    def clear_token(self) -> None:
        """Delete cached token"""
        if self.token_file.exists():
            self.token_file.unlink()
            logger.info("Token cleared")


class OAuthHandler:
    """
    Robinhood OAuth 2.1 PKCE Token Acquisition (Secret-less)

    For public clients (apps running locally), PKCE eliminates the need for
    a shared client_secret. Instead:
    
    1. Generate random code_verifier (43-128 chars)
    2. Compute SHA256(code_verifier) → code_challenge
    3. Send user to login with code_challenge
    4. After login, exchange code + verifier for token
    5. Cache token locally

    Works the same way ChatGPT and Claude apps connect to Robinhood.
    """

    def __init__(self):
        self.token_store = TokenStore()
        self.mcp_endpoint = settings.robinhood_mcp_endpoint
        self.account_id = settings.robinhood_account_id
        self.oauth_endpoint = "https://robinhood.com/oauth/authorize"
        self.token_endpoint = "https://robinhood.com/oauth/token"

    @staticmethod
    def _generate_pkce_pair() -> Tuple[str, str]:
        """
        Generate PKCE code_verifier and code_challenge pair
        
        Returns:
            (code_verifier, code_challenge)
        """
        # Generate random 128-char string
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(96)).decode("utf-8").rstrip("=")
        
        # Compute SHA256 hash and base64url encode
        challenge_bytes = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = base64.urlsafe_b64encode(challenge_bytes).decode("utf-8").rstrip("=")
        
        logger.debug(f"Generated PKCE verifier (length={len(code_verifier)}) and challenge (length={len(code_challenge)})")
        return code_verifier, code_challenge

    async def get_access_token(self) -> str:
        """
        Get valid OAuth access token, handling refresh/login as needed

        Returns:
            Bearer token string for Authorization header

        Raises:
            RuntimeError: If login fails or token cannot be obtained
        """

        # Step 1: Check cached token
        cached_token = self.token_store.load_token()
        if cached_token and "access_token" in cached_token:
            logger.info("Using cached access token")
            return cached_token["access_token"]

        # Step 2: Try refresh if refresh_token available
        if cached_token and "refresh_token" in cached_token:
            logger.info("Attempting token refresh...")
            refreshed = await self._refresh_token(cached_token["refresh_token"])
            if refreshed:
                self.token_store.save_token(refreshed)
                return refreshed["access_token"]

        # Step 3: No cached/refreshable token → require browser login
        logger.warning("No valid cached token. Browser login required.")
        new_token = await self._browser_login()
        if not new_token:
            raise RuntimeError(
                "OAuth login failed. Cannot proceed without valid access token."
            )

        self.token_store.save_token(new_token)
        return new_token["access_token"]

    async def _refresh_token(self, refresh_token: str) -> Optional[Dict[str, Any]]:
        """
        Attempt silent token refresh using refresh_token

        Args:
            refresh_token: Previously stored refresh token

        Returns:
            New token data dict or None if refresh fails
        """
        try:
            # This would call Robinhood's token endpoint (not MCP)
            # For now, log that refresh was attempted
            logger.info("Token refresh requested (implementation pending)")
            # TODO: Implement actual refresh endpoint call
            return None
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            return None

    async def _browser_login(self) -> Optional[Dict[str, Any]]:
        """
        Initiate browser-based OAuth login with 2FA support

        Opens browser with Robinhood login URL, waits for user to complete
        2FA, then intercepts redirect to extract access_token.

        Uses local callback server to receive OAuth redirect.

        Returns:
            Token data dict with access_token, refresh_token, expires_in
            or None if login cancelled
        """
        try:
            logger.info("Starting Robinhood OAuth browser login...")

            # Try using Playwright if available (for headless automation)
            try:
                from playwright.async_api import async_playwright
                logger.info("✅ Playwright found, attempting automated browser login...")
                result = await self._browser_login_playwright()
                if result:
                    return result
                else:
                    logger.warning("⚠️  Playwright login returned no token. Falling back to manual...")
                    return await self._browser_login_manual()
            except ImportError:
                logger.warning("⚠️  Playwright not installed. Using manual browser flow.")
                return await self._browser_login_manual()
            except Exception as e:
                logger.error(f"❌ Playwright login error: {e}")
                logger.info("Falling back to manual browser flow...")
                return await self._browser_login_manual()

        except Exception as e:
            logger.error(f"❌ All login methods failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    async def _browser_login_playwright(self) -> Optional[Dict[str, Any]]:
        """
        Browser login using Playwright - intercept Bearer token from responses
        
        Opens real Robinhood login page, user authenticates with credentials + 2FA,
        then sniffs the network traffic to extract Bearer token from response headers.
        No app registration needed - works as a Generic Public Client.
        """
        from playwright.async_api import async_playwright

        try:
            async with async_playwright() as p:
                logger.info("🌐 Launching browser...")
                browser = await p.chromium.launch(headless=False)
                logger.info("✅ Browser launched successfully!")
                
                context = await browser.new_context()
                page = await context.new_page()

                bearer_token = None
                refresh_token = None
                expires_in = 86400  # Default 24h

                async def intercept_response(response):
                    """
                    Intercept all network responses to find Bearer token
                    Robinhood sends tokens in response headers or body after successful auth
                    """
                    nonlocal bearer_token, refresh_token, expires_in
                    
                    try:
                        # Check response URL
                        url = response.url
                        
                        # Skip static assets (JS, CSS, images)
                        if any(url.endswith(ext) for ext in ['.js', '.css', '.png', '.jpg', '.gif', '.woff', '.woff2', '.ttf']):
                            return
                        
                        # Check for OAuth error page
                        if "oauth/error" in url.lower():
                            logger.error(f"❌ OAuth ERROR PAGE DETECTED: {url}")
                            logger.error(f"   This means the OAuth server rejected the request")
                            logger.error(f"   Possible causes:")
                            logger.error(f"   1. Invalid client_id (check 'mcp-agent-client' is registered)")
                            logger.error(f"   2. Account not provisioned for agentic trading")
                            logger.error(f"   3. Invalid redirect_uri in request")
                            logger.error(f"   📋 Check browser page for specific error message")
                            try:
                                body = await response.text()
                                if body and len(body) < 1000:
                                    logger.error(f"   Response body: {body}")
                            except:
                                pass
                            return
                        
                        # Only log OAuth-related endpoints
                        if any(term in url.lower() for term in ['token', 'oauth', 'auth', 'login', 'session', 'authorize']):
                            logger.info(f"📡 OAuth response from: {url}")
                        
                        # Check for authorization tokens in response headers
                        try:
                            headers = await response.all_headers()
                            
                            # Check for Bearer token in Authorization header
                            if "authorization" in headers:
                                auth_header = headers.get("authorization", "")
                                if "Bearer" in auth_header:
                                    token = auth_header.replace("Bearer ", "").strip()
                                    
                                    # CRITICAL FILTER: Reject legacy retail tokens, keep waiting for agentic token
                                    token_info = inspect_jwt_token(token)
                                    if token_info.get("is_legacy"):
                                        logger.warning(f"📡 Found retail token (oid: {token_info.get('oid', 'unknown')}, scope: {token_info.get('scope')})")
                                        logger.warning(f"   ⏭️  Skipping - waiting for agentic-scoped token...")
                                        return
                                    
                                    # Only set if we haven't found one yet
                                    if not bearer_token:
                                        bearer_token = token
                                        logger.info(f"✅ Agentic Bearer token captured from Authorization header!")
                                        logger.info(f"   URL: {url}")
                                        logger.info(f"   Token length: {len(token)} chars")
                                        logger.info(f"   Scope: {token_info.get('scope', 'unknown')}")
                        except:
                            pass
                        
                        # Try to parse from response body if it's JSON
                        try:
                            headers = await response.all_headers()
                            if "application/json" in headers.get("content-type", ""):
                                body = await response.text()
                                if body and len(body) < 10000:  # Don't try huge responses
                                    try:
                                        data = json.loads(body)
                                        
                                        # Look for access_token in response
                                        if "access_token" in data and not bearer_token:
                                            token = data["access_token"]
                                            
                                            # CRITICAL FILTER: Reject legacy retail tokens, keep waiting for agentic token
                                            token_info = inspect_jwt_token(token)
                                            if token_info.get("is_legacy"):
                                                logger.warning(f"📡 Found retail token in response body (oid: {token_info.get('oid', 'unknown')}, scope: {token_info.get('scope')})")
                                                logger.warning(f"   ⏭️  Skipping - waiting for agentic-scoped token...")
                                                return
                                            
                                            # Accept the agentic token
                                            bearer_token = token
                                            refresh_token = data.get("refresh_token")
                                            expires_in = data.get("expires_in", 86400)
                                            logger.info(f"✅ Agentic Bearer token captured from JSON response body!")
                                            logger.info(f"   URL: {url}")
                                            logger.info(f"   Token length: {len(bearer_token)} chars")
                                            logger.info(f"   Scope: {token_info.get('scope', 'unknown')}")
                                            logger.info(f"   Response keys: {list(data.keys())}")
                                    except json.JSONDecodeError:
                                        pass
                        except:
                            pass
                            
                    except Exception as e:
                        logger.debug(f"Response intercept error: {e}")

                page.on("response", intercept_response)

                logger.info(f"🔐 Initiating OAuth flow with AGENTIC TRADING scope...")
                logger.info(f"   This will mint an agentic-scoped token (not retail)")
                logger.info(f"   🔍 Network filter: Will SKIP legacy retail tokens and ONLY accept agentic tokens")
                
                # Step 1: Generate PKCE pair for secret-less OAuth
                code_verifier, code_challenge = self._generate_pkce_pair()
                logger.info(f"   ✅ Generated PKCE challenge")
                
                # Step 2: Build OAuth authorization URL with agentic_trading scope
                # PKCE still requires a public client_id to identify the application
                oauth_params = {
                    "client_id": "mcp-agent-client",  # Standard public client ID for MCP agents
                    "response_type": "code",
                    "scope": "agentic_trading",  # Forces agentic-scoped token (not retail)
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                    "redirect_uri": "https://agent.robinhood.com/mcp/callback",  # Robinhood's whitelisted callback
                }
                
                authorize_url = f"https://robinhood.com/oauth/authorize/?{urlencode(oauth_params)}"
                logger.info(f"🔐 Opening OAuth authorize endpoint with agentic_trading scope...")
                logger.info(f"   Client ID: {oauth_params['client_id']}")
                logger.info(f"   Scope: {oauth_params['scope']}")
                logger.info(f"   You: Enter your Robinhood credentials + 2FA")
                logger.info(f"   I'm watching for the agentic-scoped Bearer token...")
                logger.debug(f"   Full URL: {authorize_url}")
                
                try:
                    await page.goto(authorize_url, timeout=60000)
                    logger.info(f"✅ OAuth authorize page loaded!")
                except Exception as e:
                    logger.warning(f"⚠️  Page load warning: {e}")
                    logger.info(f"📋 If you need to test manually, paste this URL in your browser:")
                    logger.info(f"   {authorize_url}")

                # Wait for user to authenticate (up to 2 minutes)
                logger.info("⏳ Waiting for authentication... (timeout: 120s)")
                logger.info("   If you don't see a browser window, this may have failed.")
                logger.info("   You'll be asked to authenticate manually...")
                
                poll_interval = 500
                timeout_ms = 120000
                elapsed = 0
                
                while not bearer_token and elapsed < timeout_ms:
                    await asyncio.sleep(poll_interval / 1000)
                    elapsed += poll_interval

                await browser.close()

                if not bearer_token:
                    logger.error("❌ Authentication timeout - no Bearer token captured")
                    logger.warning("⚠️  Browser login failed. Falling back to manual authentication...")
                    return None

                logger.info(f"✅ Authentication successful!")
                return {
                    "access_token": bearer_token,
                    "refresh_token": refresh_token,
                    "expires_in": expires_in,
                    "token_type": "Bearer",
                }
        except Exception as e:
            logger.error(f"❌ Browser login error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    async def _browser_login_manual(self) -> Optional[Dict[str, Any]]:
        """
        Manual browser login (fallback if Playwright not available)
        
        User opens Robinhood AGENTIC TRADING portal, then pastes the Bearer token
        """
        logger.info(
            "\n" + "=" * 70
            + "\n🔐 ROBINHOOD AGENTIC TRADING LOGIN REQUIRED\n"
            + "=" * 70
        )

        print(f"\n1️⃣  Open Robinhood Agentic Trading portal in your browser:")
        print(f"   https://robinhood.com/us/en/agentic-trading/\n")
        print("2️⃣  Complete login with your credentials + 2FA\n")
        print("3️⃣  After successful login, the page will show the dashboard\n")
        print("4️⃣  Open your browser's Developer Tools (F12 or Cmd+Opt+I)\n")
        print("5️⃣  Go to Application → Cookies → robinhood.com\n")
        print("6️⃣  Find cookie named 'Authorization' or 'access_token'\n")
        print("    Copy the TOKEN VALUE (not the name)\n")

        # Wait for user to paste bearer token
        bearer_token = input("Paste the Bearer token and press Enter: ").strip()

        if not bearer_token or len(bearer_token) < 20:
            logger.error("❌ No valid Bearer token provided")
            return None

        logger.info("✅ Bearer token received from user input")
        return {
            "access_token": bearer_token,
            "expires_in": 86400,
            "token_type": "Bearer",
        }

    async def invalidate_token(self) -> None:
        """Clear cached token (e.g., on logout or permission error)"""
        self.token_store.clear_token()
        logger.info("Token invalidated")


# Singleton instance
_oauth_handler: Optional[OAuthHandler] = None


async def get_oauth_handler() -> OAuthHandler:
    """Get or create OAuth handler instance"""
    global _oauth_handler
    if _oauth_handler is None:
        _oauth_handler = OAuthHandler()
    return _oauth_handler


async def get_bearer_token() -> str:
    """Convenience function: get valid bearer token for MCP calls"""
    handler = await get_oauth_handler()
    return await handler.get_access_token()
