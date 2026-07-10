#!/usr/bin/env python3
"""Debug script to inspect cached OAuth token"""

import json
from pathlib import Path

token_file = Path.home() / ".robinhood_agent" / "tokens" / "access_token.json"

print(f"🔍 Checking token cache at: {token_file}")
print(f"   File exists: {token_file.exists()}")

if token_file.exists():
    with open(token_file, "r") as f:
        token_data = json.load(f)
    
    print(f"\n📋 Cached Token Data:")
    print(f"   Keys: {list(token_data.keys())}")
    
    for key, value in token_data.items():
        if key == "access_token":
            if isinstance(value, str):
                print(f"   {key}: {value[:50]}...{value[-20:] if len(value) > 70 else ''}")
                print(f"            Length: {len(value)}, Type: {type(value).__name__}")
            else:
                print(f"   {key}: {type(value).__name__} = {value}")
        else:
            print(f"   {key}: {value}")
else:
    print(f"\n❌ Token file not found. Login first with a trade request.")
