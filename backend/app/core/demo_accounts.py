"""
Demo accounts configuration and helpers.
These demo accounts sign in without a password check (see app/core/security.py).
"""
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)

# Each entry: {"name", "email", "password", "id"}.
# Keep in sync with frontend/src/lib/demoAccounts.js.
DEMO_ACCOUNTS: list[dict] = []

DEMO_ACCOUNTS_BY_EMAIL = {
    account["email"].strip().lower(): account
    for account in DEMO_ACCOUNTS
}

DEMO_ACCOUNTS_BY_ID = {
    account["id"]: account
    for account in DEMO_ACCOUNTS
}


class DemoUser:
    """User object with the same shape as app.services.auth.AuthUser."""
    def __init__(self, id: str, email: str, name: str):
        self.id = id
        self.email = email
        self.user_metadata = {
            "name": name,
            "full_name": name,
        }
        self.app_metadata = {
            "provider": "demo",
        }
        self.role = "authenticated"


def is_demo_token(token: str) -> bool:
    if not token or not isinstance(token, str):
        return False
    return token.startswith("demo-token:")


def get_demo_user_from_token(token: str) -> DemoUser | None:
    if not is_demo_token(token):
        return None
    email = token.split("demo-token:", 1)[1].strip().lower()
    acc = DEMO_ACCOUNTS_BY_EMAIL.get(email)
    if not acc:
        return None
    return DemoUser(id=acc["id"], email=acc["email"], name=acc["name"])


def ensure_demo_user_db_state(admin_client, demo_user: DemoUser):
    """
    Ensure the demo user has a profile and at least one workspace in the database
    so they can create meetings and view data immediately in the real project.
    """
    try:
        # 1. Ensure profile exists
        prof_res = (
            admin_client
            .table("profiles")
            .select("id")
            .eq("id", demo_user.id)
            .execute()
        )
        if not prof_res.data:
            admin_client.table("profiles").insert({
                "id": demo_user.id,
                "name": demo_user.user_metadata.get("name") or demo_user.email,
            }).execute()

        # 2. Ensure user has a workspace
        mem_res = (
            admin_client
            .table("workspace_members")
            .select("workspace_id")
            .eq("user_id", demo_user.id)
            .execute()
        )
        if not mem_res.data:
            ws_id = str(uuid4())
            admin_client.table("workspaces").insert({
                "id": ws_id,
                "name": f"{demo_user.user_metadata.get('name', 'Demo')}'s Workspace",
                "created_by": demo_user.id,
                "terminology": {},
            }).execute()
    except Exception as exc:
        logger.warning("Could not auto-provision demo user db state: %s", exc)
