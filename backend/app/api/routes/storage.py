"""
File uploads into MongoDB storage.

The browser uploads in three steps so every request stays small:

    POST   /api/v1/storage/{bucket}/uploads                      -> {upload_id}
    PUT    /api/v1/storage/{bucket}/uploads/{upload_id}/parts/{n}   raw bytes
    POST   /api/v1/storage/{bucket}/uploads/{upload_id}/complete  {part_count}

Objects can also be deleted:

    DELETE /api/v1/storage/{bucket}/object?path=...

Access is checked with the same rules the old storage policies used:
meeting buckets need membership of the meeting's workspace, the
template bucket needs ownership (write) or membership (read).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.security import get_db
from app.db import APIError, Client
from app.db.storage import CHUNK_SIZE


router = APIRouter(
    prefix="/api/v1/storage",
    tags=["Storage"],
)


class BeginUploadRequest(BaseModel):
    path: str
    content_type: str | None = None
    upsert: bool = False


class CompleteUploadRequest(BaseModel):
    part_count: int


def _http_error(exc: APIError) -> HTTPException:
    status_code = 400

    if exc.code and str(exc.code).isdigit():
        status_code = int(exc.code)

    if status_code < 400 or status_code > 599:
        status_code = 400

    return HTTPException(status_code=status_code, detail=exc.message)


def _bucket(db: Client, bucket: str):
    try:
        return db.storage.from_(bucket)
    except APIError as exc:
        raise _http_error(exc)


@router.post("/{bucket}/uploads")
async def begin_upload(
    bucket: str,
    payload: BeginUploadRequest,
    db: Client = Depends(get_db),
):
    try:
        document = _bucket(db, bucket).begin_upload(
            payload.path,
            payload.content_type,
            payload.upsert,
        )
    except APIError as exc:
        raise _http_error(exc)

    return {
        "upload_id": document["id"],
        "path": document["path"],
        "chunk_size": CHUNK_SIZE,
    }


@router.put("/{bucket}/uploads/{upload_id}/parts/{part_number}")
async def upload_part(
    bucket: str,
    upload_id: str,
    part_number: int,
    request: Request,
    db: Client = Depends(get_db),
):
    data = await request.body()

    try:
        _bucket(db, bucket).put_part(upload_id, part_number, data)
    except APIError as exc:
        raise _http_error(exc)

    return {"part_number": part_number, "size": len(data)}


@router.post("/{bucket}/uploads/{upload_id}/complete")
async def complete_upload(
    bucket: str,
    upload_id: str,
    payload: CompleteUploadRequest,
    db: Client = Depends(get_db),
):
    try:
        return _bucket(db, bucket).complete_upload(upload_id, payload.part_count)
    except APIError as exc:
        raise _http_error(exc)


@router.delete("/{bucket}/uploads/{upload_id}")
async def abort_upload(
    bucket: str,
    upload_id: str,
    db: Client = Depends(get_db),
):
    try:
        _bucket(db, bucket).abort_upload(upload_id)
    except APIError as exc:
        raise _http_error(exc)

    return {"message": "Upload aborted"}


@router.delete("/{bucket}/object")
async def delete_object(
    bucket: str,
    path: str,
    db: Client = Depends(get_db),
):
    try:
        removed = _bucket(db, bucket).remove([path])
    except APIError as exc:
        raise _http_error(exc)

    return {"removed": removed}
