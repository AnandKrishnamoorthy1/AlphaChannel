#!/usr/bin/env python3
"""
Quick Qwen API diagnostic script to test the connection and API key.

Run this to debug Qwen API issues:
    python test_qwen_api.py
"""

import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger
import dashscope
from dashscope import Generation

# Configure logging
logger.remove()
logger.add(lambda msg: print(msg, end=""), format="<level>{message}</level>")

from config.settings import settings

def test_qwen_connection():
    """Test Qwen API connection."""
    print("\n" + "="*80)
    print("QWEN API DIAGNOSTIC TEST")
    print("="*80 + "\n")
    
    # Check settings
    print("1. Configuration Check:")
    print(f"   - QWEN_API_KEY loaded: {bool(settings.qwen_api_key)}")
    print(f"   - QWEN_API_KEY length: {len(settings.qwen_api_key) if settings.qwen_api_key else 0}")
    if settings.qwen_api_key:
        print(f"   - QWEN_API_KEY starts with: {settings.qwen_api_key[:20]}...")
    print(f"   - QWEN_MODEL: {settings.qwen_model}")
    print(f"   - QWEN_TEMPERATURE: {settings.qwen_temperature}")
    print(f"   - QWEN_TOP_P: {settings.qwen_top_p}")
    
    # Set API key
    print("\n2. API Key Setup:")
    if settings.qwen_api_key:
        dashscope.api_key = settings.qwen_api_key
        print(f"   ✓ API key set in dashscope: {dashscope.api_key[:20]}...")
    else:
        print("   ✗ No API key available!")
        return False
    
    # Test simple call
    print("\n3. Simple API Call Test:")
    try:
        test_messages = [
            {
                "role": "system",
                "content": "You are a helpful assistant."
            },
            {
                "role": "user",
                "content": "Say hello!"
            }
        ]
        
        print(f"   Model: {settings.qwen_model}")
        print(f"   Calling Generation.call()...")
        
        response = Generation.call(
            model=settings.qwen_model,
            messages=test_messages,
            temperature=0.7,
            top_p=0.85,
        )
        
        print(f"   Response status: {response.status_code}")
        print(f"   Response type: {type(response)}")
        print(f"   Response dir: {[attr for attr in dir(response) if not attr.startswith('_')]}")
        
        if response.status_code == 200:
            print(f"   ✓ API call successful!")
            print(f"   Response: {response.output.text[:100]}...")
            return True
        else:
            print(f"   ✗ API error: {response.status_code}")
            if hasattr(response, 'code'):
                print(f"      Code: {response.code}")
            if hasattr(response, 'message'):
                print(f"      Message: {response.message}")
            return False
            
    except Exception as e:
        print(f"   ✗ Exception: {type(e).__name__}: {str(e)}")
        import traceback
        print("\n   Full traceback:")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_qwen_connection()
    print("\n" + "="*80)
    if success:
        print("✓ QWEN API TEST PASSED")
    else:
        print("✗ QWEN API TEST FAILED")
        print("\nPossible solutions:")
        print("1. Verify QWEN_API_KEY is correct in .env file")
        print("2. Check that the API key hasn't expired")
        print("3. Verify you have credits/quota in your Alibaba account")
        print("4. Try uncommenting the international endpoint in qwen_integration.py")
    print("="*80 + "\n")
    sys.exit(0 if success else 1)
