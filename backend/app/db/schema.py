"""
Per-collection behaviour ported from the original Postgres schema:
primary keys, column defaults, timestamp columns, immutable columns
(the old protect_*_identity triggers) and updated_at maintenance.
"""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class TableSpec:
    # Columns that identify a row. Generated as a UUID when the
    # primary key is the single column "id".
    primary_key: tuple[str, ...] = ("id",)

    # Static defaults (callables are invoked per row).
    defaults: dict[str, Any | Callable[[], Any]] = field(default_factory=dict)

    # Columns set to now() on insert when absent.
    created_columns: tuple[str, ...] = ("created_at",)

    # Maintain updated_at on insert and every update.
    has_updated_at: bool = True

    # Extra timestamp columns (stored as BSON dates, returned as ISO strings).
    timestamp_columns: tuple[str, ...] = ()

    # Columns that may never change after insert.
    immutable_columns: tuple[str, ...] = ()


def _list() -> list:
    return []


def _dict() -> dict:
    return {}


TABLES: dict[str, TableSpec] = {
    "profiles": TableSpec(
        immutable_columns=("id",),
    ),
    "workspaces": TableSpec(
        defaults={"terminology": _dict},
        immutable_columns=("id", "created_by"),
    ),
    "workspace_members": TableSpec(
        primary_key=("workspace_id", "user_id"),
        defaults={"role": "member"},
        created_columns=("joined_at",),
        has_updated_at=False,
        immutable_columns=("workspace_id", "user_id"),
    ),
    "workspace_invitations": TableSpec(
        defaults={"status": "pending"},
        has_updated_at=False,
        timestamp_columns=("expires_at", "accepted_at"),
    ),
    "workspace_mom_templates": TableSpec(
        defaults={"name": "MOM Template", "source_type": "template"},
    ),
    "meetings": TableSpec(
        defaults={"status": "draft"},
        timestamp_columns=("meeting_date",),
        immutable_columns=("id", "workspace_id", "created_by"),
    ),
    "meeting_participants": TableSpec(
        defaults={"sort_order": 0},
    ),
    "moms": TableSpec(
        defaults={
            "status": "draft",
            "key_discussion_points": _list,
            "decisions": _list,
            "next_steps": _list,
            "abbreviations_used": _dict,
        },
        timestamp_columns=("generated_at",),
        immutable_columns=("id", "meeting_id", "created_by"),
    ),
    "action_items": TableSpec(
        defaults={"priority": "medium", "status": "pending"},
        timestamp_columns=("due_date", "completed_at"),
        immutable_columns=("id", "mom_id", "meeting_id", "created_by"),
    ),
    "attachments": TableSpec(
        defaults={"attachment_type": "meeting_attachment"},
        has_updated_at=False,
        immutable_columns=("id", "meeting_id", "uploaded_by", "file_path"),
    ),
    "processing_jobs": TableSpec(
        defaults={"status": "queued", "output_data": _dict},
        timestamp_columns=("started_at", "completed_at"),
        immutable_columns=("id", "meeting_id", "created_by"),
    ),
    "online_meeting_integrations": TableSpec(
        timestamp_columns=("last_synced_at",),
    ),
    "google_connections": TableSpec(
        primary_key=("user_id",),
        defaults={"granted_scopes": _list},
    ),
    "zoom_connections": TableSpec(
        primary_key=("user_id",),
        defaults={"granted_scopes": _list},
    ),
    "fireflies_connections": TableSpec(
        defaults={"is_active": True},
        created_columns=("created_at", "connected_at"),
        timestamp_columns=("last_sync_at",),
    ),
    "fireflies_meetings": TableSpec(
        defaults={
            "status": "pending",
            "participants": _list,
            "raw_metadata": _dict,
        },
        created_columns=("created_at", "imported_at"),
        timestamp_columns=("fireflies_date",),
    ),
    # Auth collection. Only reachable through the admin client.
    "users": TableSpec(
        timestamp_columns=("last_sign_in_at",),
        immutable_columns=("id",),
    ),
}


# Every column of the original Postgres tables. Rows always come back with
# all of them (missing ones as None), like a SQL row would.
COLUMNS: dict[str, tuple[str, ...]] = {
    "action_items": (
        "id",
        "mom_id",
        "meeting_id",
        "created_by",
        "task",
        "assigned_to",
        "assigned_name",
        "due_date",
        "priority",
        "completed_at",
        "google_event_id",
        "created_at",
        "updated_at",
        "status",
    ),
    "attachments": (
        "id",
        "meeting_id",
        "uploaded_by",
        "file_name",
        "file_path",
        "file_type",
        "file_size",
        "attachment_type",
        "created_at",
    ),
    "fireflies_connections": (
        "id",
        "workspace_id",
        "connected_by",
        "fireflies_user_email",
        "is_active",
        "connected_at",
        "last_sync_at",
        "updated_at",
        "api_key_encrypted",
        "created_at",
    ),
    "fireflies_meetings": (
        "id",
        "workspace_id",
        "meeting_id",
        "fireflies_transcript_id",
        "original_title",
        "organizer_email",
        "transcript_url",
        "meeting_link",
        "participants",
        "fireflies_date",
        "raw_metadata",
        "imported_at",
        "created_at",
        "updated_at",
        "duration_minutes",
        "status",
        "error_message",
    ),
    "google_connections": (
        "user_id",
        "refresh_token_encrypted",
        "granted_scopes",
        "created_at",
        "updated_at",
    ),
    "meeting_participants": (
        "id",
        "meeting_id",
        "name",
        "email",
        "role",
        "sort_order",
        "created_by",
        "created_at",
        "updated_at",
    ),
    "meetings": (
        "id",
        "workspace_id",
        "created_by",
        "title",
        "description",
        "meeting_date",
        "source_file_path",
        "audio_file_path",
        "video_file_path",
        "duration_seconds",
        "created_at",
        "updated_at",
        "venue",
        "mode",
        "status",
    ),
    "moms": (
        "id",
        "meeting_id",
        "created_by",
        "title",
        "summary",
        "transcript",
        "key_discussion_points",
        "decisions",
        "next_steps",
        "abbreviations_used",
        "generated_at",
        "created_at",
        "updated_at",
        "status",
    ),
    "online_meeting_integrations": (
        "id",
        "meeting_id",
        "provider",
        "external_space_name",
        "meeting_uri",
        "meeting_code",
        "conference_record_name",
        "transcript_resource_name",
        "transcript_state",
        "last_synced_at",
        "created_at",
        "updated_at",
    ),
    "processing_jobs": (
        "id",
        "meeting_id",
        "created_by",
        "error_message",
        "input_file_path",
        "output_data",
        "source_attachment_id",
        "output_mom_id",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
        "status",
        "job_type",
    ),
    "profiles": (
        "id",
        "name",
        "created_at",
        "updated_at",
    ),
    "workspace_invitations": (
        "id",
        "workspace_id",
        "email",
        "invited_by",
        "token_hash",
        "status",
        "expires_at",
        "created_at",
        "accepted_at",
        "accepted_by",
    ),
    "workspace_members": (
        "workspace_id",
        "user_id",
        "joined_at",
        "role",
    ),
    "workspace_mom_templates": (
        "id",
        "workspace_id",
        "name",
        "created_by",
        "created_at",
        "updated_at",
        "file_name",
        "file_path",
        "file_type",
        "file_size",
        "source_type",
    ),
    "workspaces": (
        "id",
        "name",
        "created_by",
        "terminology",
        "created_at",
        "updated_at",
    ),
    "zoom_connections": (
        "user_id",
        "refresh_token_encrypted",
        "granted_scopes",
        "created_at",
        "updated_at",
    ),
}


def get_spec(table: str) -> TableSpec:
    return TABLES.get(table) or TableSpec()


def timestamp_columns(table: str) -> set[str]:
    spec = get_spec(table)

    columns = set(spec.timestamp_columns) | set(spec.created_columns)

    if spec.has_updated_at:
        columns.add("updated_at")

    return columns
