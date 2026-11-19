"""
A small query builder over MongoDB.

It keeps the fluent `.table(...).select(...).eq(...).execute()` call shape the
routes were written against, so the data access code reads the same while
everything is stored in MongoDB:

    db.table("meetings").select("id,title").eq("workspace_id", ws).execute()

Each collection stores one document per row. Rows keep their UUID string `id`
(Mongo's own `_id` is never exposed). Timestamp columns are stored as BSON
dates and returned as ISO-8601 strings, as Postgres used to return them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.db.schema import COLUMNS, get_spec, timestamp_columns

if TYPE_CHECKING:
    from app.db.client import DataClient


# ============================================================
# RESULTS AND ERRORS
# ============================================================

class APIError(Exception):
    """Raised for constraint, policy and shape errors."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        details: Any = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details

    def __str__(self) -> str:
        if self.code:
            return f"{self.message} (code {self.code})"
        return self.message


class APIResponse:
    def __init__(self, data: Any = None, count: int | None = None):
        self.data = data
        self.count = count

    def __repr__(self) -> str:
        return f"APIResponse(data={self.data!r}, count={self.count!r})"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# VALUE CONVERSION
# ============================================================

def _parse_timestamp(value: Any) -> Any:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)

    if isinstance(value, str) and value.strip():
        text = value.strip()

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return value

        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    return value


def _to_storage_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, dict):
        return {str(k): _to_storage_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_to_storage_value(v) for v in value]

    return value


def to_storage_row(table: str, row: dict) -> dict:
    ts_columns = timestamp_columns(table)
    result = {}

    for key, value in row.items():
        value = _to_storage_value(value)

        if key in ts_columns:
            value = _parse_timestamp(value)

        result[key] = value

    return result


def _to_output_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    if isinstance(value, dict):
        return {k: _to_output_value(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_to_output_value(v) for v in value]

    return value


def to_output_row(
    document: dict,
    columns: list[str] | None = None,
    table: str | None = None,
) -> dict:
    document = {k: v for k, v in document.items() if k != "_id"}

    if columns is not None:
        document = {c: document.get(c) for c in columns}
    elif table in COLUMNS:
        for column in COLUMNS[table]:
            document.setdefault(column, None)

    return _to_output_value(document)


def _sort_key(value: Any):
    # Postgres semantics: NULLs sort after every value in ascending order.
    if value is None:
        return (1, 0, "")

    if isinstance(value, bool):
        return (0, 0, int(value))

    if isinstance(value, (int, float)):
        return (0, 0, value)

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (0, 1, value.timestamp())

    return (0, 2, str(value))


# ============================================================
# SELECT PARSING (columns and embedded relations)
# ============================================================

# Many-to-one relations that may be embedded in a select, PostgREST style:
#   .table("workspace_members").select("role, workspaces(id, name)")
# (table, embedded name) -> (local column, foreign table, foreign column)
RELATIONS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("workspace_members", "workspaces"): ("workspace_id", "workspaces", "id"),
    ("workspace_members", "profiles"): ("user_id", "profiles", "id"),
    ("meetings", "workspaces"): ("workspace_id", "workspaces", "id"),
    ("moms", "meetings"): ("meeting_id", "meetings", "id"),
    ("action_items", "meetings"): ("meeting_id", "meetings", "id"),
    ("action_items", "moms"): ("mom_id", "moms", "id"),
    ("attachments", "meetings"): ("meeting_id", "meetings", "id"),
    ("processing_jobs", "meetings"): ("meeting_id", "meetings", "id"),
    ("workspace_invitations", "workspaces"): ("workspace_id", "workspaces", "id"),
}


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, []

    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1

        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)

    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def parse_select(text: str) -> tuple[list[str] | None, dict[str, str]]:
    """Return (plain columns or None for *, {embed name: inner select})."""
    columns: list[str] = []
    embeds: dict[str, str] = {}
    star = False

    for part in _split_top_level(" ".join(str(text or "*").split())):
        if "(" in part and part.endswith(")"):
            name, inner = part.split("(", 1)
            name = name.strip().split("!")[0].strip()
            embeds[name] = inner[:-1]
        elif part == "*":
            star = True
        else:
            columns.append(part)

    return (None if star or not columns else columns), embeds


# ============================================================
# QUERY BUILDER
# ============================================================

class QueryBuilder:
    def __init__(self, client: "DataClient", table: str):
        self._client = client
        self._table = table
        self._operation = "select"
        self._columns: list[str] | None = None
        self._embeds: dict[str, str] = {}
        self._count: str | None = None
        self._payload: Any = None
        self._on_conflict: list[str] | None = None
        self._filters: list[dict] = []
        self._order: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._offset: int = 0
        self._single: str | None = None

    @property
    def _db(self) -> Database:
        return self._client.database

    @property
    def _collection(self):
        return self._db[self._table]

    # --------------------------------------------------------
    # Operations
    # --------------------------------------------------------

    def select(self, columns: str = "*", count: str | None = None, **_: Any):
        self._columns, self._embeds = parse_select(columns)
        self._count = count
        return self

    def insert(self, data: dict | list[dict], **_: Any):
        self._operation = "insert"
        self._payload = data
        return self

    def upsert(
        self,
        data: dict | list[dict],
        on_conflict: str | None = None,
        **_: Any,
    ):
        self._operation = "upsert"
        self._payload = data
        self._on_conflict = (
            [c.strip() for c in on_conflict.split(",") if c.strip()]
            if on_conflict
            else None
        )
        return self

    def update(self, data: dict, **_: Any):
        self._operation = "update"
        self._payload = data
        return self

    def delete(self, **_: Any):
        self._operation = "delete"
        return self

    # --------------------------------------------------------
    # Filters
    # --------------------------------------------------------

    def _value(self, column: str, value: Any) -> Any:
        value = _to_storage_value(value)

        if column in timestamp_columns(self._table):
            value = _parse_timestamp(value)

        return value

    def eq(self, column: str, value: Any):
        self._filters.append({column: self._value(column, value)})
        return self

    def neq(self, column: str, value: Any):
        self._filters.append({column: {"$ne": self._value(column, value)}})
        return self

    def gt(self, column: str, value: Any):
        self._filters.append({column: {"$gt": self._value(column, value)}})
        return self

    def gte(self, column: str, value: Any):
        self._filters.append({column: {"$gte": self._value(column, value)}})
        return self

    def lt(self, column: str, value: Any):
        self._filters.append({column: {"$lt": self._value(column, value)}})
        return self

    def lte(self, column: str, value: Any):
        self._filters.append({column: {"$lte": self._value(column, value)}})
        return self

    def in_(self, column: str, values: list[Any]):
        self._filters.append(
            {column: {"$in": [self._value(column, v) for v in values]}}
        )
        return self

    def is_(self, column: str, value: Any):
        if value is None or str(value).lower() == "null":
            self._filters.append({column: None})
        else:
            self._filters.append({column: self._value(column, value)})
        return self

    # --------------------------------------------------------
    # Modifiers
    # --------------------------------------------------------

    def order(self, column: str, desc: bool = False, **_: Any):
        self._order.append((column, bool(desc)))
        return self

    def limit(self, count: int, **_: Any):
        self._limit = int(count)
        return self

    def range(self, start: int, end: int, **_: Any):
        self._offset = int(start)
        self._limit = int(end) - int(start) + 1
        return self

    def single(self):
        self._single = "single"
        return self

    def maybe_single(self):
        self._single = "maybe"
        return self

    # --------------------------------------------------------
    # Execution
    # --------------------------------------------------------

    def execute(self) -> APIResponse:
        try:
            if self._operation == "select":
                return self._execute_select()
            if self._operation == "insert":
                return self._execute_insert(upsert=False)
            if self._operation == "upsert":
                return self._execute_insert(upsert=True)
            if self._operation == "update":
                return self._execute_update()
            if self._operation == "delete":
                return self._execute_delete()
        except DuplicateKeyError as exc:
            raise APIError(
                f'duplicate key value violates unique constraint on "{self._table}"',
                code="23505",
                details=str(exc),
            ) from exc

        raise APIError(f"Unsupported operation: {self._operation}")

    def _query_filter(self, scope: dict | None) -> dict:
        clauses = list(self._filters)

        if scope:
            clauses.append(scope)

        if not clauses:
            return {}

        if len(clauses) == 1:
            return clauses[0]

        return {"$and": clauses}

    def _find(self, scope: dict | None) -> list[dict]:
        query = self._query_filter(scope)

        if not self._order:
            cursor = self._collection.find(query)

            if self._offset:
                cursor = cursor.skip(self._offset)

            if self._limit is not None:
                cursor = cursor.limit(self._limit)

            return list(cursor)

        # Ordering is done here (not in Mongo) to keep Postgres NULL
        # ordering: NULLS LAST for ascending, NULLS FIRST for descending.
        documents = list(self._collection.find(query))

        for column, desc in reversed(self._order):
            documents.sort(
                key=lambda doc, c=column: _sort_key(doc.get(c)),
                reverse=desc,
            )

        end = None if self._limit is None else self._offset + self._limit
        return documents[self._offset:end]

    def _resolve_embeds(self, documents: list[dict], rows: list[dict]) -> None:
        for name, inner in self._embeds.items():
            relation = RELATIONS.get((self._table, name))

            if relation is None:
                raise APIError(
                    f"Could not find a relationship between '{self._table}' and '{name}'",
                    code="PGRST200",
                )

            local, foreign_table, foreign = relation
            keys = list({doc.get(local) for doc in documents if doc.get(local) is not None})

            related: dict[Any, dict] = {}

            if keys:
                inner_columns, _ = parse_select(inner)
                query = self._client.table(foreign_table).select("*").in_(foreign, keys)
                # The embedded table's own access rules apply, as in PostgREST.
                for item in query.execute().data or []:
                    related[item.get(foreign)] = (
                        {c: item.get(c) for c in inner_columns}
                        if inner_columns
                        else item
                    )

            for doc, row in zip(documents, rows):
                row[name] = related.get(doc.get(local))

    def _shape(self, documents: list[dict]) -> APIResponse:
        rows = [to_output_row(doc, self._columns, self._table) for doc in documents]

        if self._embeds:
            self._resolve_embeds(documents, rows)

        count = None

        if self._single == "single":
            if len(rows) != 1:
                raise APIError(
                    "JSON object requested, multiple (or no) rows returned",
                    code="PGRST116",
                    details=f"The result contains {len(rows)} rows",
                )
            return APIResponse(rows[0], count)

        if self._single == "maybe":
            if len(rows) > 1:
                raise APIError(
                    "JSON object requested, multiple rows returned",
                    code="PGRST116",
                )
            return APIResponse(rows[0] if rows else None, count)

        return APIResponse(rows, count)

    # SELECT --------------------------------------------------

    def _execute_select(self) -> APIResponse:
        scope = self._client.read_scope(self._table)

        if scope is False:
            response = self._shape([])
            if self._count:
                response.count = 0
            return response

        documents = self._find(scope)
        response = self._shape(documents)

        if self._count:
            response.count = self._collection.count_documents(
                self._query_filter(scope)
            )

        return response

    # INSERT / UPSERT ----------------------------------------

    def _prepare_new_row(self, row: dict) -> dict:
        spec = get_spec(self._table)
        now = utcnow()
        prepared = to_storage_row(self._table, dict(row))

        for column in COLUMNS.get(self._table, ()):
            prepared.setdefault(column, None)

        if spec.primary_key == ("id",) and not prepared.get("id"):
            prepared["id"] = str(uuid.uuid4())

        for column, default in spec.defaults.items():
            if prepared.get(column) is None:
                prepared[column] = default() if callable(default) else default

        for column in spec.created_columns:
            if prepared.get(column) is None:
                prepared[column] = now

        if spec.has_updated_at and prepared.get("updated_at") is None:
            prepared["updated_at"] = now

        return prepared

    def _execute_insert(self, upsert: bool) -> APIResponse:
        rows = self._payload if isinstance(self._payload, list) else [self._payload]
        rows = [r for r in rows if r is not None]

        if not rows:
            return APIResponse([])

        spec = get_spec(self._table)
        conflict_columns = self._on_conflict or list(spec.primary_key)
        results: list[dict] = []

        for raw in rows:
            if upsert:
                key = {
                    c: _to_storage_value(raw.get(c))
                    for c in conflict_columns
                    if raw.get(c) is not None
                }
                existing = (
                    self._collection.find_one(key)
                    if len(key) == len(conflict_columns)
                    else None
                )

                if existing:
                    changes = {
                        k: v for k, v in raw.items()
                        if k not in conflict_columns
                    }
                    updated = (
                        self._client.table(self._table)
                        .update(changes)
                        ._with_filters(key)
                        ._execute_update()
                    )
                    results.extend(updated.data or [])
                    continue

            row = self._prepare_new_row(raw)
            self._client.check_insert(self._table, row)
            self._client.triggers.before_insert(self._table, row)
            self._collection.insert_one(row)
            self._client.triggers.after_insert(self._table, row)
            results.append(to_output_row(row, self._columns, self._table))

        return APIResponse(results)

    def _with_filters(self, filters: dict):
        for column, value in filters.items():
            self._filters.append({column: value})
        return self

    # UPDATE --------------------------------------------------

    def _execute_update(self) -> APIResponse:
        changes = to_storage_row(self._table, dict(self._payload or {}))
        spec = get_spec(self._table)

        scope = self._client.write_scope(self._table, "update")

        if scope is False:
            return APIResponse([])

        documents = list(self._collection.find(self._query_filter(scope)))

        if not documents:
            return APIResponse([])

        for doc in documents:
            for column in spec.immutable_columns:
                if column in changes and changes[column] != doc.get(column):
                    raise APIError(
                        f"{self._table}.{column} cannot be changed",
                        code="P0001",
                    )
            self._client.triggers.before_update(self._table, doc, changes)

        if spec.has_updated_at:
            changes["updated_at"] = utcnow()

        changes.pop("_id", None)

        ids = [doc["_id"] for doc in documents]

        if changes:
            self._collection.update_many(
                {"_id": {"$in": ids}},
                {"$set": changes},
            )

        updated = list(self._collection.find({"_id": {"$in": ids}}))
        return APIResponse([to_output_row(doc, self._columns, self._table) for doc in updated])

    # DELETE --------------------------------------------------

    def _execute_delete(self) -> APIResponse:
        scope = self._client.write_scope(self._table, "delete")

        if scope is False:
            return APIResponse([])

        documents = list(self._collection.find(self._query_filter(scope)))

        if not documents:
            return APIResponse([])

        for doc in documents:
            self._client.triggers.before_delete(self._table, doc)

        self._collection.delete_many(
            {"_id": {"$in": [doc["_id"] for doc in documents]}}
        )

        return APIResponse([to_output_row(doc, self._columns, self._table) for doc in documents])


# ============================================================
# STORED PROCEDURES
# ============================================================

class RpcCall:
    def __init__(self, client: "DataClient", name: str, params: dict | None):
        self._client = client
        self._name = name
        self._params = params or {}

    def execute(self) -> APIResponse:
        handler = getattr(self, f"_rpc_{self._name}", None)

        if handler is None:
            raise APIError(f"Unknown function: {self._name}", code="PGRST202")

        return APIResponse(handler(**self._params))

    def _rpc_claim_fireflies_sync(self, target_workspace_id: str) -> bool:
        now = utcnow()

        claimed = self._client.database["fireflies_connections"].find_one_and_update(
            {
                "workspace_id": str(target_workspace_id),
                "is_active": True,
                "$or": [
                    {"last_sync_at": None},
                    {"last_sync_at": {"$lte": now - timedelta(hours=1)}},
                ],
            },
            {"$set": {"last_sync_at": now, "updated_at": now}},
        )

        return claimed is not None

    def _rpc_complete_processing_job(
        self,
        target_job_id: str,
        target_status: str,
        target_output_data: dict | None = None,
        target_error_message: str | None = None,
    ) -> bool:
        collection = self._client.database["processing_jobs"]
        job = collection.find_one({"id": str(target_job_id)})

        if not job:
            return False

        now = utcnow()
        status = _to_storage_value(target_status)

        changes: dict[str, Any] = {
            "status": status,
            "output_data": _to_storage_value(target_output_data or {}),
            "error_message": target_error_message,
            "updated_at": now,
        }

        if status in ("completed", "failed"):
            changes["completed_at"] = now

        if status == "processing" and not job.get("started_at"):
            changes["started_at"] = now

        collection.update_one({"_id": job["_id"]}, {"$set": changes})
        return True
