"""
DataClient: the object routes receive to talk to the database.

    get_admin_db()          trusted server-side client, no access rules
    get_user_db(user)       client scoped to one user (old RLS behaviour)
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.db.mongo import get_database
from app.db.policies import AccessPolicy
from app.db.query import APIError, QueryBuilder, RpcCall
from app.db.storage import StorageClient
from app.db.triggers import Triggers


class DataClient:
    def __init__(
        self,
        database: Database,
        user_id: str | None = None,
        auth_user: Any = None,
    ):
        self.database = database
        self.user_id = str(user_id) if user_id else None
        self.auth_user = auth_user
        self.triggers = Triggers(database)
        self._policy = AccessPolicy(database, self.user_id) if self.user_id else None
        self.storage = StorageClient(self)

    @property
    def is_admin(self) -> bool:
        return self._policy is None

    # --------------------------------------------------------
    # Entry points
    # --------------------------------------------------------

    def table(self, name: str) -> QueryBuilder:
        return QueryBuilder(self, name)

    def from_(self, name: str) -> QueryBuilder:
        return self.table(name)

    def rpc(self, name: str, params: dict | None = None) -> RpcCall:
        # Both functions were SECURITY DEFINER; claim_fireflies_sync was
        # additionally restricted to the service role.
        if name == "claim_fireflies_sync" and not self.is_admin:
            raise APIError("permission denied for function", code="42501")

        return RpcCall(self, name, params)

    # --------------------------------------------------------
    # Access rules (used by QueryBuilder / StorageBucket)
    # --------------------------------------------------------

    def read_scope(self, table: str) -> dict | None | bool:
        """None = unrestricted, False = denied, dict = extra filter."""
        if self._policy is None:
            return None

        scope = self._policy.read_filter(table)
        return False if scope is None else scope

    def write_scope(self, table: str, operation: str) -> dict | None | bool:
        if self._policy is None:
            return None

        read = self._policy.read_filter(table)
        write = self._policy.write_filter(table, operation)

        if read is None or write is None:
            return False

        return {"$and": [read, write]}

    def check_insert(self, table: str, row: dict) -> None:
        if self._policy is None:
            return

        if not self._policy.can_insert(table, row):
            raise APIError(
                f'new row violates row-level security policy for table "{table}"',
                code="42501",
            )

    def can_access_object(self, bucket: str, path: str, write: bool) -> bool:
        if self._policy is None:
            return True

        return self._policy.can_access_object(bucket, path, write)


def get_admin_db() -> DataClient:
    return DataClient(get_database())


def get_user_db(user: Any) -> DataClient:
    return DataClient(get_database(), user_id=user.id, auth_user=user)
