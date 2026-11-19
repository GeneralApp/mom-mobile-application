"""
Ports of the Postgres triggers and foreign-key actions.

- New workspace  -> creator becomes its owner (handle_new_workspace)
- Action items   -> MOM/meeting must match, assignee must be a workspace
                    member (validate_action_item_*)
- Deletes        -> ON DELETE CASCADE / SET NULL along the old foreign keys

Triggers always run against the raw database, like SECURITY DEFINER
functions and FK actions did, so they are not limited by access rules.
"""

from typing import Any

from pymongo.database import Database

from app.db.query import APIError, utcnow


# child collection, its column, action ("cascade" | "set_null")
_FOREIGN_KEYS: dict[str, list[tuple[str, str, str]]] = {
    "workspaces": [
        ("meetings", "workspace_id", "cascade"),
        ("workspace_members", "workspace_id", "cascade"),
        ("workspace_invitations", "workspace_id", "cascade"),
        ("workspace_mom_templates", "workspace_id", "cascade"),
        ("fireflies_connections", "workspace_id", "cascade"),
        ("fireflies_meetings", "workspace_id", "cascade"),
    ],
    "meetings": [
        ("moms", "meeting_id", "cascade"),
        ("action_items", "meeting_id", "cascade"),
        ("attachments", "meeting_id", "cascade"),
        ("processing_jobs", "meeting_id", "cascade"),
        ("meeting_participants", "meeting_id", "cascade"),
        ("online_meeting_integrations", "meeting_id", "cascade"),
        ("fireflies_meetings", "meeting_id", "cascade"),
    ],
    "moms": [
        ("action_items", "mom_id", "cascade"),
        ("processing_jobs", "output_mom_id", "set_null"),
    ],
    "attachments": [
        ("processing_jobs", "source_attachment_id", "set_null"),
    ],
    "profiles": [
        ("workspace_members", "user_id", "cascade"),
        ("action_items", "assigned_to", "set_null"),
    ],
    "users": [
        ("profiles", "id", "cascade"),
        ("google_connections", "user_id", "cascade"),
        ("zoom_connections", "user_id", "cascade"),
        ("workspace_invitations", "invited_by", "cascade"),
        ("workspace_invitations", "accepted_by", "set_null"),
        ("password_reset_tokens", "user_id", "cascade"),
    ],
}


class Triggers:
    def __init__(self, database: Database):
        self.database = database

    # --------------------------------------------------------
    # INSERT
    # --------------------------------------------------------

    def before_insert(self, table: str, row: dict) -> None:
        if table == "action_items":
            self._validate_action_item(row)

    def after_insert(self, table: str, row: dict) -> None:
        if table == "workspaces" and row.get("created_by"):
            self.database["workspace_members"].update_one(
                {
                    "workspace_id": row["id"],
                    "user_id": row["created_by"],
                },
                {
                    "$set": {"role": "owner"},
                    "$setOnInsert": {"joined_at": utcnow()},
                },
                upsert=True,
            )

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def before_update(self, table: str, existing: dict, changes: dict) -> None:
        if table == "action_items":
            merged = {**existing, **changes}
            if "mom_id" in changes or "meeting_id" in changes or changes.get("assigned_to"):
                self._validate_action_item(merged)

    def _validate_action_item(self, row: dict) -> None:
        mom = self.database["moms"].find_one(
            {"id": row.get("mom_id")},
            {"meeting_id": 1},
        )

        if not mom:
            raise APIError("Invalid MOM reference", code="P0001")

        if mom.get("meeting_id") != row.get("meeting_id"):
            raise APIError(
                "Action item meeting_id must match its MOM meeting_id",
                code="P0001",
            )

        assigned_to = row.get("assigned_to")

        if not assigned_to:
            return

        meeting = self.database["meetings"].find_one(
            {"id": row.get("meeting_id")},
            {"workspace_id": 1},
        )

        if not meeting:
            raise APIError("Invalid meeting reference", code="P0001")

        is_member = self.database["workspace_members"].count_documents(
            {
                "workspace_id": meeting.get("workspace_id"),
                "user_id": assigned_to,
            },
            limit=1,
        )

        if not is_member:
            raise APIError(
                "Assigned user must be a member of the meeting workspace",
                code="P0001",
            )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def before_delete(self, table: str, row: dict) -> None:
        for child, column, action in _FOREIGN_KEYS.get(table, []):
            key = self._key_value(table, row)

            if key is None:
                continue

            if action == "set_null":
                self.database[child].update_many(
                    {column: key},
                    {"$set": {column: None}},
                )
                continue

            children = list(self.database[child].find({column: key}))

            for doc in children:
                self.before_delete(child, doc)

            if children:
                self.database[child].delete_many(
                    {"_id": {"$in": [doc["_id"] for doc in children]}}
                )

    @staticmethod
    def _key_value(table: str, row: dict) -> Any:
        return row.get("id")
