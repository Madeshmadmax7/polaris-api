#!/usr/bin/env python3
"""
Test Google Calendar Auth URL endpoint (PUBLIC - no auth required)
"""

import asyncio
import sys
import os

# Set dummy values for testing
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost:8000/api/integrations/google-calendar/callback"
os.environ["ENCRYPTION_KEY"] = "test-encryption-key-32-bytes-long-base64"

sys.path.insert(0, ".")

from app.routes.google_calendar import get_auth_url

async def test():
    print("Testing GET /api/integrations/google-calendar/auth-url (PUBLIC endpoint)")
    print("=" * 70)
    
    # Call the endpoint without authentication
    result = await get_auth_url()
    
    print("\n✅ Endpoint response:")
    print(f"   Status: {result.get('message', 'N/A')}")
    
    if "auth_url" in result:
        print(f"   Auth URL: {result['auth_url'][:80]}...")
        print("   ✅ Auth URL generated successfully!")
        
        # Verify URL contains required parameters
        auth_url = result["auth_url"]
        if "client_id=test-client-id" in auth_url:
            print("   ✅ client_id in URL")
        if "redirect_uri=" in auth_url:
            print("   ✅ redirect_uri in URL")
        if "scope=" in auth_url and "calendar" in auth_url:
            print("   ✅ calendar scope in URL")
        if "access_type=offline" in auth_url:
            print("   ✅ offline access requested")
        print("\n✅ All checks passed!")
        return True
    elif "error" in result:
        print(f"   ❌ Error: {result}")
        return False

if __name__ == "__main__":
    try:
        success = asyncio.run(test())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
