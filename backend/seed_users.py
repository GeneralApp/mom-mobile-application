"""
Create (or refresh) the demo-account users in MongoDB.

Each user gets the fixed id from app/core/demo_accounts.py so their
existing demo data lines up, a password login, and a profile.

    python seed_users.py
"""
import sys
from pathlib import Path

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from app.core.demo_accounts import DEMO_ACCOUNTS, DEMO_ACCOUNTS_BY_EMAIL
from app.db import get_admin_db
from app.services.auth import (
    authenticate,
    create_user,
    find_user_by_email,
    normalize_email,
    set_password,
)

# Users to seed: the configured demo accounts.
USERS_DATA = DEMO_ACCOUNTS


def seed_users():
    admin = get_admin_db()

    print(f"Database: {admin.database.name}")
    print(f"Total users to process: {len(USERS_DATA)}")
    print("-" * 70)

    results = []

    for idx, item in enumerate(USERS_DATA, 1):
        name = item["name"]
        email = normalize_email(item["email"])
        password = item["password"]
        demo = DEMO_ACCOUNTS_BY_EMAIL.get(email)

        try:
            existing = find_user_by_email(email)

            if existing:
                set_password(existing["id"], password)
                admin.table("users").update(
                    {"user_metadata": {"name": name, "full_name": name}}
                ).eq("id", existing["id"]).execute()
                user_id = existing["id"]
                status = "Updated existing user"
            else:
                created = create_user(
                    email,
                    password,
                    name,
                    user_id=demo["id"] if demo else None,
                )
                user_id = created["id"]
                status = "Created new user"

            profile = admin.table("profiles").select("id").eq("id", user_id).execute()

            if profile.data:
                admin.table("profiles").update({"name": name}).eq("id", user_id).execute()
            else:
                admin.table("profiles").insert({"id": user_id, "name": name}).execute()

            authenticate(email, password)
            results.append({"email": email, "user_id": user_id, "ok": True})
            print(f"[{idx:02d}/{len(USERS_DATA):02d}] {status}: {name} <{email}> -> login verified")

        except Exception as exc:
            results.append({"email": email, "user_id": None, "ok": False})
            print(f"[{idx:02d}/{len(USERS_DATA):02d}] ERROR for {name} <{email}>: {exc}")

    print("-" * 70)
    success_count = sum(1 for r in results if r["ok"])
    print(f"Successfully created/verified logins: {success_count}/{len(USERS_DATA)}")
    return results


if __name__ == "__main__":
    seed_users()
