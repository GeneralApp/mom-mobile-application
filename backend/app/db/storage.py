"""
File storage in MongoDB.

Files are stored as an object document plus ordered binary chunks:

    storage_objects  {id, bucket, path, directory, name, content_type,
                      size, part_count, complete, created_at, updated_at}
    storage_chunks   {object_id, n, data}

Chunks stay well below the 16 MB BSON document limit. The browser uploads
files in parts (see app/api/routes/storage.py) so each HTTP request stays
small enough for serverless request-size limits.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Iterable

from bson.binary import Binary

from app.db.query import APIError, utcnow

if TYPE_CHECKING:
    from app.db.client import DataClient


CHUNK_SIZE = 4 * 1024 * 1024

ALLOWED_BUCKETS = {
    "meeting-files",
    "meeting-audio",
    "meeting-video",
    "workspace-mom-template",
}

STALE_UPLOAD_AGE = timedelta(days=1)


def _split_path(path: str) -> tuple[str, str]:
    clean = str(path or "").strip().strip("/")

    if not clean or ".." in clean.split("/"):
        raise APIError("Invalid storage path", code="400")

    directory, _, name = clean.rpartition("/")
    return directory, name


class StorageClient:
    def __init__(self, client: "DataClient"):
        self._client = client

    def from_(self, bucket: str) -> "StorageBucket":
        if bucket not in ALLOWED_BUCKETS:
            raise APIError(f"Bucket not found: {bucket}", code="404")

        return StorageBucket(self._client, bucket)


class StorageBucket:
    def __init__(self, client: "DataClient", bucket: str):
        self._client = client
        self.bucket = bucket

    @property
    def _objects(self):
        return self._client.database["storage_objects"]

    @property
    def _chunks(self):
        return self._client.database["storage_chunks"]

    def _check(self, path: str, write: bool) -> None:
        if not self._client.can_access_object(self.bucket, path, write):
            raise APIError(
                "new row violates row-level security policy",
                code="403",
            )

    def _find(self, path: str, complete_only: bool = True) -> dict | None:
        query = {"bucket": self.bucket, "path": str(path).strip("/")}

        if complete_only:
            query["complete"] = True

        return self._objects.find_one(query)

    def exists(self, path: str) -> bool:
        self._check(path, write=False)
        return self._find(path) is not None

    # --------------------------------------------------------
    # Multipart uploads
    # --------------------------------------------------------

    def begin_upload(
        self,
        path: str,
        content_type: str | None = None,
        upsert: bool = False,
    ) -> dict:
        directory, name = _split_path(path)
        path = f"{directory}/{name}" if directory else name
        self._check(path, write=True)
        self._cleanup_stale_uploads()

        if not upsert and self._find(path) is not None:
            raise APIError("The resource already exists", code="409")

        now = utcnow()
        document = {
            "id": str(uuid.uuid4()),
            "bucket": self.bucket,
            "path": path,
            "directory": directory,
            "name": name,
            "content_type": content_type or "application/octet-stream",
            "size": 0,
            "part_count": 0,
            "complete": False,
            "upsert": bool(upsert),
            "created_at": now,
            "updated_at": now,
        }

        self._objects.insert_one(document)
        return document

    def get_pending_upload(self, upload_id: str) -> dict:
        document = self._objects.find_one(
            {"id": upload_id, "bucket": self.bucket, "complete": False}
        )

        if not document:
            raise APIError("Upload not found", code="404")

        self._check(document["path"], write=True)
        return document

    def put_part(self, upload_id: str, part_number: int, data: bytes) -> None:
        document = self.get_pending_upload(upload_id)

        if part_number < 0:
            raise APIError("Invalid part number", code="400")

        if len(data) > CHUNK_SIZE * 2:
            raise APIError("Upload part is too large", code="413")

        self._chunks.update_one(
            {"object_id": document["id"], "n": int(part_number)},
            {"$set": {"data": Binary(data), "size": len(data)}},
            upsert=True,
        )

    def complete_upload(self, upload_id: str, part_count: int) -> dict:
        document = self.get_pending_upload(upload_id)

        parts = list(
            self._chunks.find(
                {"object_id": document["id"]},
                {"n": 1, "size": 1},
            )
        )
        numbers = sorted(p["n"] for p in parts)

        if numbers != list(range(int(part_count))):
            raise APIError(
                "Upload is incomplete: missing parts",
                code="400",
            )

        size = sum(p.get("size", 0) for p in parts)

        # Replace any previous object at the same path.
        previous = self._find(document["path"])

        if previous is not None:
            if not document.get("upsert"):
                self._delete_objects([document])
                raise APIError("The resource already exists", code="409")
            self._delete_objects([previous])

        self._objects.update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    "complete": True,
                    "size": size,
                    "part_count": int(part_count),
                    "updated_at": utcnow(),
                }
            },
        )

        document.update(complete=True, size=size, part_count=int(part_count))
        return self._describe(document)

    def abort_upload(self, upload_id: str) -> None:
        document = self.get_pending_upload(upload_id)
        self._delete_objects([document])

    # --------------------------------------------------------
    # upload / download / list / remove, used by backend services
    # --------------------------------------------------------

    def upload(
        self,
        path: str,
        file: bytes,
        file_options: dict | None = None,
    ) -> dict:
        options = file_options or {}
        upsert = str(options.get("upsert", "false")).lower() == "true"
        content_type = (
            options.get("content-type")
            or options.get("contentType")
            or options.get("content_type")
        )

        document = self.begin_upload(path, content_type, upsert)
        data = bytes(file)
        part_count = 0

        for offset in range(0, max(len(data), 1), CHUNK_SIZE):
            self.put_part(document["id"], part_count, data[offset:offset + CHUNK_SIZE])
            part_count += 1

        self.complete_upload(document["id"], part_count)
        return {"path": document["path"], "id": document["id"]}

    def download(self, path: str) -> bytes:
        self._check(path, write=False)
        document = self._find(path)

        if document is None:
            raise APIError("Object not found", code="404")

        return b"".join(self.iter_chunks(document))

    def iter_chunks(self, document: dict) -> Iterable[bytes]:
        for chunk in self._chunks.find(
            {"object_id": document["id"]}
        ).sort("n", 1):
            yield bytes(chunk["data"])

    def get_object(self, path: str) -> dict:
        self._check(path, write=False)
        document = self._find(path)

        if document is None:
            raise APIError("Object not found", code="404")

        return document

    def list(self, path: str = "", options: dict | None = None) -> list[dict]:
        directory = str(path or "").strip("/")

        documents = self._objects.find(
            {
                "bucket": self.bucket,
                "directory": directory,
                "complete": True,
            }
        ).sort("name", 1)

        results = []

        for document in documents:
            if self._client.can_access_object(self.bucket, document["path"], write=False):
                results.append(self._describe(document))

        return results

    def remove(self, paths: list[str]) -> list[dict]:
        removed = []

        for path in paths or []:
            self._check(path, write=True)
            document = self._find(path)

            if document is not None:
                self._delete_objects([document])
                removed.append(self._describe(document))

        return removed

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------

    def _delete_objects(self, documents: list[dict]) -> None:
        ids = [doc["id"] for doc in documents]

        if not ids:
            return

        self._chunks.delete_many({"object_id": {"$in": ids}})
        self._objects.delete_many({"id": {"$in": ids}})

    def _cleanup_stale_uploads(self) -> None:
        stale = list(
            self._objects.find(
                {
                    "complete": False,
                    "created_at": {"$lt": utcnow() - STALE_UPLOAD_AGE},
                },
                {"id": 1},
            ).limit(50)
        )
        self._delete_objects(stale)

    @staticmethod
    def _describe(document: dict) -> dict:
        created = document.get("created_at")
        updated = document.get("updated_at")

        return {
            "id": document.get("id"),
            "name": document.get("name"),
            "bucket_id": document.get("bucket"),
            "path": document.get("path"),
            "created_at": created.isoformat() if created else None,
            "updated_at": updated.isoformat() if updated else None,
            "metadata": {
                "size": document.get("size"),
                "mimetype": document.get("content_type"),
            },
        }
