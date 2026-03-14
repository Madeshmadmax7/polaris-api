"""Full API test suite for LifeOS backend."""
import requests

BASE = "http://localhost:8000/api"
token = ""

def test(name, method, url, **kwargs):
    global token
    try:
        r = getattr(requests, method)(url, **kwargs)
        status = r.status_code
        ok = status < 400
        symbol = "[OK]" if ok else "[FAIL]"
        print(f"  {symbol} {name}: {status}")
        if not ok:
            print(f"       Response: {r.text[:200]}")
        return r
    except Exception as e:
        print(f"  [ERR] {name}: {e}")
        return None

print("=" * 50)
print("LifeOS API Test Suite")
print("=" * 50)

# 1. Health
print("\n--- Health ---")
test("Health Check", "get", "http://localhost:8000/health")

# 2. Auth
print("\n--- Auth ---")
r = test("Register (new user)", "post", f"{BASE}/auth/register", json={
    "email": "apitest@test.com",
    "username": "apitest",
    "password": "testpass123",
    "role": "student"
})
if r and r.status_code == 201:
    token = r.json()["access_token"]
elif r and r.status_code == 409:
    # User exists, login instead
    r = test("Login (existing)", "post", f"{BASE}/auth/login", json={
        "email": "apitest@test.com",
        "password": "testpass123",
    })
    if r and r.status_code == 200:
        token = r.json()["access_token"]

headers = {"Authorization": f"Bearer {token}"}

test("Login", "post", f"{BASE}/auth/login", json={
    "email": "apitest@test.com",
    "password": "testpass123",
})
test("Get Me", "get", f"{BASE}/auth/me", headers=headers)

# 3. Productivity
print("\n--- Productivity ---")
test("Today Score", "get", f"{BASE}/productivity/today", headers=headers)
test("Trend (7 days)", "get", f"{BASE}/productivity/trend?days=7", headers=headers)

# 4. Tracking
print("\n--- Tracking ---")
test("Log Activity", "post", f"{BASE}/tracking/log", headers=headers, json={
    "domain": "github.com",
    "duration_seconds": 300,
    "tab_switches": 2,
    "scroll_depth": 0.5,
    "is_active": True,
})
test("Get Logs", "get", f"{BASE}/tracking/logs", headers=headers)
test("Get Domains", "get", f"{BASE}/tracking/domains", headers=headers)
test("Set Category", "post", f"{BASE}/tracking/categories", headers=headers, json={
    "domain_pattern": "example.com",
    "category": "productive"
})

# 5. AI (will fail with dummy keys - expected)
print("\n--- AI (dummy keys, errors expected) ---")
test("List Documents", "get", f"{BASE}/ai/documents", headers=headers)
test("List Study Plans", "get", f"{BASE}/ai/study-plans", headers=headers)
test("List Quizzes", "get", f"{BASE}/ai/quizzes", headers=headers)

# 6. Parental (student account - some will fail with 403)
print("\n--- Parental (as student) ---")
test("Accept Invite (no code)", "post", f"{BASE}/parental/accept-invite?invite_code=NOCODE", headers=headers)

# Register parent
print("\n--- Parent Registration ---")
r = test("Register Parent", "post", f"{BASE}/auth/register", json={
    "email": "parent@test.com",
    "username": "parentuser",
    "password": "testpass123",
    "role": "parent"
})
if r and r.status_code == 201:
    parent_token = r.json()["access_token"]
    parent_headers = {"Authorization": f"Bearer {parent_token}"}
    test("List Children", "get", f"{BASE}/parental/children", headers=parent_headers)
elif r and r.status_code == 409:
    print("  [INFO] Parent already exists")

print("\n" + "=" * 50)
print("Test complete!")
print("=" * 50)
