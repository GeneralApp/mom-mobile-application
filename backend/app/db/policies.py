"""
Row-level access rules for user-scoped data clients.

This is a port of the Postgres row-level-security policies the app used to
rely on. A user-scoped DataClient applies these rules to every query, so a
route that queries through the request's client can only see and change what
the caller was allowed to before. The admin client skips them entirely.

Each rule returns a MongoDB filter that is AND-ed with the query's own
filters, or None when the operation is not allowed at all.
"""

from typing import Any

from pymongo.database import Database


MEETING_SCOPED_TABLES = {
    "moms",
    "action_items",
    "attachments",
    "processing_jobs",
    "meeting_participants",
}

WORKSPACE_SCOPED_READ_TABLES = {
    "meetings",
    "workspace_members",
    "workspace_mom_templates",
    "fireflies_connections",
    "fireflies_meetings",
}


class AccessPolicy:
    def __init__(self, database: Database, user_id: str):
        self.database = database
        self.user_id = str(user_id)

    # --------------------------------------------------------
    # Membership helpers (the old is_workspace_* SQL functions)
    # --------------------------------------------------------

    def workspace_ids(self, roles: tuple[str, ...] | None = None) -> list[str]:
        query: dict[str, Any] = {"user_id": self.user_id}

        if roles:
            query["role"] = {"$in": list(roles)}

        return self.database["workspace_members"].distinct(
            "workspace_id",
            query,
        )

    def meeting_ids(self, workspace_ids: list[str]) -> list[str]:
        if not workspace_ids:
            return []

        return self.database["meetings"].distinct(
            "id",
            {"workspace_id": {"$in": workspace_ids}},
        )

    def is_member(self, workspace_id: str | None) -> bool:
        return bool(workspace_id) and self._has_role(workspace_id, None)

    def is_owner(self, workspace_id: str | None) -> bool:
        return bool(workspace_id) and self._has_role(workspace_id, ("owner",))

    def is_admin(self, workspace_id: str | None) -> bool:
        return bool(workspace_id) and self._has_role(
            workspace_id,
            ("owner", "admin"),
        )

    def _has_role(self, workspace_id: str, roles: tuple[str, ...] | None) -> bool:
        query: dict[str, Any] = {
            "workspace_id": str(workspace_id),
            "user_id": self.user_id,
        }

        if roles:
            query["role"] = {"$in": list(roles)}

        return self.database["workspace_members"].count_documents(
            query,
            limit=1,
        ) > 0

    def meeting_workspace(self, meeting_id: str | None) -> str | None:
        if not meeting_id:
            return None

        meeting = self.database["meetings"].find_one(
            {"id": str(meeting_id)},
            {"workspace_id": 1},
        )

        return meeting.get("workspace_id") if meeting else None

    # --------------------------------------------------------
    # SELECT
    # --------------------------------------------------------

    def read_filter(self, table: str) -> dict | None:
        uid = self.user_id

        if table == "profiles":
            member_ws = self.workspace_ids()
            co_members = (
                self.database["workspace_members"].distinct(
                    "user_id",
                    {"workspace_id": {"$in": member_ws}},
                )
                if member_ws
                else []
            )
            return {"id": {"$in": list({uid, *co_members})}}

        if table == "workspaces":
            return {"id": {"$in": self.workspace_ids()}}

        if table in WORKSPACE_SCOPED_READ_TABLES:
            return {"workspace_id": {"$in": self.workspace_ids()}}

        if table in MEETING_SCOPED_TABLES:
            return {
                "meeting_id": {
                    "$in": self.meeting_ids(self.workspace_ids())
                }
            }

        # google_connections, zoom_connections, online_meeting_integrations,
        # workspace_invitations, users, ... are server-only.
        return None

    # --------------------------------------------------------
    # UPDATE / DELETE
    # --------------------------------------------------------

    def write_filter(self, table: str, operation: str) -> dict | None:
        uid = self.user_id

        if table == "profiles":
            return {"id": uid} if operation == "update" else None

        if table == "workspaces":
            return {"id": {"$in": self.workspace_ids(("owner",))}}

        if table in ("workspace_members", "workspace_mom_templates"):
            return {"workspace_id": {"$in": self.workspace_ids(("owner",))}}

        if table == "meetings":
            return {
                "$or": [
                    {"created_by": uid},
                    {"workspace_id": {"$in": self.workspace_ids(("owner",))}},
                ]
            }

        if table in ("moms", "action_items", "meeting_participants"):
            owner_meetings = self.meeting_ids(self.workspace_ids(("owner",)))
            return {
                "$or": [
                    {"created_by": uid},
                    {"meeting_id": {"$in": owner_meetings}},
                ]
            }

        if table == "attachments":
            if operation == "update":
                return {"uploaded_by": uid}

            admin_meetings = self.meeting_ids(
                self.workspace_ids(("owner", "admin"))
            )
            return {
                "$or": [
                    {"uploaded_by": uid},
                    {"meeting_id": {"$in": admin_meetings}},
                ]
            }

        return None

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    def can_insert(self, table: str, row: dict) -> bool:
        uid = self.user_id

        if table == "profiles":
            return str(row.get("id")) == uid

        if table == "workspaces":
            return str(row.get("created_by")) == uid

        if table == "workspace_members":
            return self.is_owner(row.get("workspace_id"))

        if table == "workspace_mom_templates":
            return (
                str(row.get("created_by")) == uid
                and self.is_owner(row.get("workspace_id"))
            )

        if table == "meetings":
            return (
                str(row.get("created_by")) == uid
                and self.is_member(row.get("workspace_id"))
            )

        if table in ("moms", "action_items", "processing_jobs", "meeting_participants"):
            return (
                str(row.get("created_by")) == uid
                and self.is_member(self.meeting_workspace(row.get("meeting_id")))
            )

        if table == "attachments":
            return (
                str(row.get("uploaded_by")) == uid
                and self.is_member(self.meeting_workspace(row.get("meeting_id")))
            )

        return False

    # --------------------------------------------------------
    # STORAGE (the old storage.objects policies)
    # --------------------------------------------------------

    MEETING_BUCKETS = {"meeting-files", "meeting-audio", "meeting-video"}
    TEMPLATE_BUCKET = "workspace-mom-template"

    def can_access_object(self, bucket: str, path: str, write: bool) -> bool:
        parts = str(path or "").split("/")

        if bucket in self.MEETING_BUCKETS:
            if len(parts) < 3:
                return False

            workspace_id, meeting_id = parts[0], parts[1]

            meeting = self.database["meetings"].find_one(
                {"id": meeting_id, "workspace_id": workspace_id},
                {"_id": 1},
            )

            return bool(meeting) and self.is_member(workspace_id)

        if bucket == self.TEMPLATE_BUCKET:
            if len(parts) < 2:
                return False

            workspace_id = parts[0]

            return (
                self.is_owner(workspace_id)
                if write
                else self.is_member(workspace_id)
            )

        return False
