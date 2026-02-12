#!/usr/bin/env python3
"""
Test script for Planner Service (llama-server)

Tests that llama-server is running and can handle vision requests
via the OpenAI-compatible API.
"""

import asyncio
import base64
import json
import sys
from io import BytesIO
from PIL import Image, ImageDraw
import aiohttp


async def create_test_image() -> str:
    """Create a simple test screenshot"""
    img = Image.new('RGB', (1280, 720), color='white')
    draw = ImageDraw.Draw(img)
    
    # Header
    draw.rectangle([0, 0, 1280, 80], fill='#2196F3')
    draw.text((20, 30), "Test Website", fill='white')
    
    # Login button
    draw.rectangle([1100, 20, 1240, 60], fill='#4CAF50')
    draw.text((1130, 30), "Login", fill='white')
    
    # Main content
    draw.rectangle([50, 150, 1230, 600], outline='#CCCCCC', width=2)
    draw.text((60, 160), "Welcome! Please click Login to continue.", fill='black')
    
    buffer = BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


async def test_health(base_url: str) -> bool:
    """Test llama-server health endpoint"""
    print("Testing llama-server health...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/health") as response:
                if response.status == 200:
                    data = await response.json()
                    status = data.get("status", "unknown")
                    print(f"✓ Server healthy (status: {status})")
                    return True
                else:
                    print(f"✗ Health check failed: {response.status}")
                    return False
    except aiohttp.ClientError as e:
        print(f"✗ Cannot connect to server: {e}")
        return False


async def test_text_completion(base_url: str) -> bool:
    """Test basic text completion"""
    print("\nTesting text completion...")
    
    payload = {
        "model": "qwen3-vl",
        "messages": [
            {"role": "user", "content": "Hello, what model are you? Reply in one sentence."}
        ],
        "max_tokens": 64,
        "temperature": 0.7
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/v1/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    content = data["choices"][0]["message"]["content"]
                    print(f"✓ Text completion works")
                    print(f"  Response: {content[:150]}")
                    return True
                else:
                    error = await response.text()
                    print(f"✗ Text completion failed: {response.status}")
                    print(f"  Error: {error[:200]}")
                    return False
    except Exception as e:
        print(f"✗ Text completion error: {e}")
        return False


async def test_vision_completion(base_url: str, screenshot_b64: str) -> bool:
    """Test vision completion with image"""
    print("\nTesting vision completion (with screenshot)...")
    
    payload = {
        "model": "qwen3-vl",
        "messages": [
            {
                "role": "system",
                "content": "You are a web navigation agent. Respond with valid JSON only."
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{screenshot_b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": (
                            "Task: Click the login button.\n\n"
                            "Respond with ONLY a JSON object:\n"
                            '{"action_type": "CLICK", "target_description": "...", '
                            '"input_value": "", "reasoning": "...", "outcome_mismatch": ""}'
                        )
                    }
                ]
            }
        ],
        "max_tokens": 256,
        "temperature": 0.7
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/v1/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    content = data["choices"][0]["message"]["content"]
                    print(f"✓ Vision completion works")
                    print(f"  Raw output: {content[:300]}")
                    
                    # Try to parse as JSON
                    try:
                        # Strip markdown fences if present
                        clean = content.strip()
                        if clean.startswith("```"):
                            lines = clean.split("\n")
                            lines = [l for l in lines if not l.strip().startswith("```")]
                            clean = "\n".join(lines).strip()
                        
                        action = json.loads(clean)
                        print(f"✓ Valid JSON output")
                        print(f"  Action: {action.get('action_type')}")
                        print(f"  Target: {action.get('target_description', '')[:100]}")
                        print(f"  Reasoning: {action.get('reasoning', '')[:100]}")
                        return True
                    except json.JSONDecodeError:
                        print(f"⚠ Output is not valid JSON (may need prompt tuning)")
                        return True  # Server works, just JSON parsing issue
                else:
                    error = await response.text()
                    print(f"✗ Vision completion failed: {response.status}")
                    print(f"  Error: {error[:200]}")
                    return False
    except Exception as e:
        print(f"✗ Vision completion error: {e}")
        return False


async def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
    
    print("=" * 60)
    print("Planner Service Test (llama-server)")
    print("=" * 60)
    print(f"Testing: {base_url}")
    print()
    
    # Test 1: Health
    health_ok = await test_health(base_url)
    if not health_ok:
        print("\n✗ Server not reachable. Make sure llama-server is running.")
        sys.exit(1)
    
    # Test 2: Text completion
    text_ok = await test_text_completion(base_url)
    
    # Test 3: Vision completion
    print("\nCreating test screenshot...")
    screenshot = await create_test_image()
    print("✓ Test screenshot created")
    
    vision_ok = await test_vision_completion(base_url, screenshot)
    
    # Summary
    print()
    print("=" * 60)
    results = {"Health": health_ok, "Text": text_ok, "Vision": vision_ok}
    all_ok = all(results.values())
    
    for name, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    
    print()
    if all_ok:
        print("✓ All tests passed!")
        print()
        print("Your llama-server planner is ready.")
        print("Set PLANNER_BACKEND=qwen in your .env to use it.")
    else:
        print("✗ Some tests failed. Check the output above.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())