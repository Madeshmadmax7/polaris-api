#!/usr/bin/env python3
"""
Quick test to verify the calendar status endpoint works
"""

import asyncio
import sys
sys.path.insert(0, ".")

from app.routes.google_calendar import get_calendar_status

async def test():
    result = await get_calendar_status()
    print("✅ Endpoint response:")
    print(result)
    assert result["connected"] == False
    assert "message" in result
    print("✅ All checks passed!")

if __name__ == "__main__":
    asyncio.run(test())
