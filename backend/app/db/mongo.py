"""
MongoDB connection management.

A single MongoClient is shared per process (it is thread-safe and pools
connections). It is created lazily so importing the app never blocks on the
network, which keeps serverless cold starts cheap.
"""

import logging
import threading

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from app.core.config import settings


logger = logging.getLogger(__name__)

_client: MongoClient | None = None
_client_lock = threading.Lock()
_index_lock = threading.Lock()
_indexes_ready = False


def get_mongo_client() -> MongoClient:
    global _client

    if _client is None:
        with _client_lock:
            if _client is None:
                if not settings.mongodb_uri:
                    raise RuntimeError("MONGODB_URI is not configured")

                _client = MongoClient(
                    settings.mongodb_uri,
                    tz_aware=True,
                    serverSelectionTimeoutMS=10000,
                    appname="mom-mobile-application",
                )

    return _client


def get_database() -> Database:
    database = get_mongo_client()[settings.mongodb_db_name]
    ensure_indexes(database)
    return database


# ============================================================
# INDEXES
# ============================================================
#
# These mirror the primary keys, unique constraints and lookup
# indexes of the original Postgres schema.

_UNIQUE_INDEXES: dict[str, list[list[str]]] = {
    "users": [["id"], ["email"]],
    "profiles": [["id"]],
    "workspaces": [["id"]],
    "workspace_members": [["workspace_id", "user_id"]],
    "workspace_invitations": [["id"], ["token_hash"]],
    "workspace_mom_templates": [["id"], ["workspace_id"]],
    "meetings": [["id"]],
    "meeting_participants": [["id"]],
    "moms": [["id"], ["meeting_id"]],
    "action_items": [["id"]],
    "attachments": [["id"], ["file_path"]],
    "processing_jobs": [["id"]],
    "online_meeting_integrations": [["id"], ["meeting_id"]],
    "google_connections": [["user_id"]],
    "zoom_connections": [["user_id"]],
    "fireflies_connections": [["id"], ["workspace_id"]],
    "fireflies_meetings": [
        ["id"],
        ["meeting_id"],
        ["fireflies_transcript_id"],
    ],
    "password_reset_tokens": [["token_hash"]],
    "storage_objects": [["id"]],
    "storage_chunks": [["object_id", "n"]],
}

_LOOKUP_INDEXES: dict[str, list[list[tuple[str, int]]]] = {
    "workspaces": [[("created_by", ASCENDING)]],
    "workspace_members": [[("user_id", ASCENDING)]],
    "workspace_invitations": [
        [("workspace_id", ASCENDING)],
        [("email", ASCENDING)],
        [("status", ASCENDING)],
    ],
    "meetings": [
        [("workspace_id", ASCENDING), ("created_at", DESCENDING)],
        [("created_by", ASCENDING)],
        [("status", ASCENDING)],
        [("meeting_date", ASCENDING)],
    ],
    "meeting_participants": [
        [("meeting_id", ASCENDING), ("sort_order", ASCENDING), ("created_at", ASCENDING)],
    ],
    "moms": [[("created_by", ASCENDING)], [("status", ASCENDING)]],
    "action_items": [
        [("meeting_id", ASCENDING)],
        [("mom_id", ASCENDING)],
        [("assigned_to", ASCENDING)],
        [("status", ASCENDING)],
        [("due_date", ASCENDING)],
        [("google_event_id", ASCENDING)],
    ],
    "attachments": [[("meeting_id", ASCENDING)], [("uploaded_by", ASCENDING)]],
    "processing_jobs": [
        [("meeting_id", ASCENDING)],
        [("status", ASCENDING)],
        [("created_by", ASCENDING)],
        [("output_mom_id", ASCENDING)],
        [("source_attachment_id", ASCENDING)],
    ],
    "online_meeting_integrations": [
        [("provider", ASCENDING)],
        [("conference_record_name", ASCENDING)],
    ],
    "fireflies_meetings": [
        [("workspace_id", ASCENDING)],
        [("fireflies_date", DESCENDING)],
    ],
    "password_reset_tokens": [[("user_id", ASCENDING)]],
    "storage_objects": [
        [("bucket", ASCENDING), ("path", ASCENDING)],
        [("bucket", ASCENDING), ("directory", ASCENDING)],
    ],
}

# Collections that should exist even before any document is written,
# so the database is visible (and complete) right after setup.
ALL_COLLECTIONS = sorted(
    set(_UNIQUE_INDEXES) | set(_LOOKUP_INDEXES)
)


def ensure_indexes(database: Database, force: bool = False) -> None:
    global _indexes_ready

    if _indexes_ready and not force:
        return

    with _index_lock:
        if _indexes_ready and not force:
            return

        try:
            existing = set(database.list_collection_names())

            for name in ALL_COLLECTIONS:
                if name not in existing:
                    database.create_collection(name)

            for collection, index_list in _UNIQUE_INDEXES.items():
                for fields in index_list:
                    database[collection].create_index(
                        [(field, ASCENDING) for field in fields],
                        unique=True,
                        name="uniq_" + "_".join(fields),
                    )

            for collection, index_list in _LOOKUP_INDEXES.items():
                for keys in index_list:
                    database[collection].create_index(keys)

            # Expire used/expired password reset tokens automatically.
            database["password_reset_tokens"].create_index(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
                name="ttl_expires_at",
            )

            _indexes_ready = True

        except Exception:
            # Index creation must never take the API down; it is retried
            # on the next request.
            logger.exception("Failed to ensure MongoDB indexes")
