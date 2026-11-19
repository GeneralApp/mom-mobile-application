"""
Manual smoke test: sign in against the running API and print the session.

    set TEST_USER_EMAIL / TEST_USER_PASSWORD (and optionally API_BASE_URL)
    python test_auth.py
"""
import os

import httpx

base_url = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
email = os.environ.get("TEST_USER_EMAIL", "test@example.com")
password = os.environ.get("TEST_USER_PASSWORD", "")

if not password:
    print("Set TEST_USER_EMAIL and TEST_USER_PASSWORD to run test_auth.py")
    exit(0)

response = httpx.post(
    f"{base_url}/api/v1/auth/login",
    json={"email": email, "password": password},
    timeout=30,
)

if response.status_code != 200:
    raise RuntimeError(f"Login failed: {response.status_code} {response.text}")

session = response.json()

print("USER UUID:")
print(session["user"]["id"])

print("\nEMAIL:")
print(session["user"]["email"])

print("\nACCESS TOKEN:")
print(session["access_token"])
