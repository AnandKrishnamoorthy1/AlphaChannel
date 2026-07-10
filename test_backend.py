#!/usr/bin/env python3
"""Quick test of the FastAPI backend endpoints."""

import json
from fastapi.testclient import TestClient
from backend import app

def main():
    """Run tests against FastAPI backend."""
    client = TestClient(app)
    
    print("\n" + "=" * 80)
    print("BACKEND API TEST SUITE")
    print("=" * 80 + "\n")
    
    # Test 1: Health Check
    print("TEST 1: Health Check")
    print("-" * 80)
    response = client.get("/health")
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}\n")
    
    # Test 2: Analyze with Research Query
    print("TEST 2: Analyze with Research Query")
    print("-" * 80)
    request_data = {"user_input": "Analyze NVDA earnings"}
    response = client.post("/api/analyze", json=request_data)
    print(f"Status Code: {response.status_code}")
    data = response.json()
    print(f"Status: {data.get('status')}")
    print(f"Routing Decision: {data.get('routing_decision')}")
    print(f"Execution Time: {data.get('execution_time_ms'):.1f}ms")
    print(f"Number of Log Entries: {len(data.get('logs', []))}")
    print(f"Report Generated: {len(data.get('report_markdown', '')) > 0}")
    print("\nFirst 5 Log Entries:")
    for i, log in enumerate(data.get("logs", [])[:5], 1):
        print(f"  {i}. {log[:70]}..." if len(log) > 70 else f"  {i}. {log}")
    print()
    
    # Test 3: Analyze with General Question
    print("TEST 3: Analyze with General Question")
    print("-" * 80)
    request_data = {"user_input": "What are the top ML frameworks in 2024?"}
    response = client.post("/api/analyze", json=request_data)
    print(f"Status Code: {response.status_code}")
    data = response.json()
    print(f"Status: {data.get('status')}")
    print(f"Routing Decision: {data.get('routing_decision')}")
    print(f"Execution Time: {data.get('execution_time_ms'):.1f}ms")
    print()
    
    # Test 4: Get Routes
    print("TEST 4: Get Available Routes")
    print("-" * 80)
    response = client.get("/api/routes")
    print(f"Status Code: {response.status_code}")
    routes = response.json()
    for route in routes.get("routes", []):
        print(f"  Route: {route['path']}")
        print(f"  Description: {route['description']}")
        print(f"  Triggers: {', '.join(route['triggers'][:3])}")
        print()
    
    print("=" * 80)
    print("ALL TESTS COMPLETED ✓")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
