from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.db import Client
from app.db import get_admin_db

from app.core.security import (
    get_access_token,
    get_db,
    get_authenticated_user,
)


router = APIRouter(
    prefix="/api/v1/attachments",
    tags=["Attachments"],
)


ALLOWED_BUCKETS = {
    "meeting-files",
    "meeting-audio",
    "meeting-video",
    "workspace-mom-template",
}


class UploadConfirmRequest(BaseModel):
    meeting_id: str | None = None
    workspace_id: str | None = None
    bucket: str
    path: str
    file_name: str
    file_type: str | None = None
    file_size: int | None = None
    attachment_type: str = "meeting_attachment"


@router.post("/confirm")
async def confirm_upload(
    payload: UploadConfirmRequest,
    db: Client = Depends(get_db),
    access_token: str = Depends(get_access_token),
):
    # --------------------------------------------------------
    # Authenticate user
    # --------------------------------------------------------

    user = get_authenticated_user(db, access_token)
    user_id = user.id

    # --------------------------------------------------------
    # Validate bucket
    # --------------------------------------------------------

    if payload.bucket not in ALLOWED_BUCKETS:
        raise HTTPException(
            status_code=400,
            detail="Invalid storage bucket",
        )

    # --------------------------------------------------------
    # Validate attachment type
    # --------------------------------------------------------

    if payload.attachment_type not in {
        "meeting_attachment",
        "imported_mom",
        "workspace_mom_template",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid attachment type",
        )

    # --------------------------------------------------------
    # Get meeting
    # --------------------------------------------------------

    meeting_response = (
        db
        .table("meetings")
        .select("id,workspace_id")
        .eq("id", payload.meeting_id)
        .execute()
    )

    if not meeting_response.data:
        raise HTTPException(
            status_code=404,
            detail="Meeting not found",
        )

    meeting = meeting_response.data[0]

    # --------------------------------------------------------
    # Validate storage path
    # --------------------------------------------------------

    expected_prefix = (
        f"{meeting['workspace_id']}/"
        f"{meeting['id']}/"
    )

    if not payload.path.startswith(expected_prefix):
        raise HTTPException(
            status_code=400,
            detail="Invalid storage path",
        )

    # --------------------------------------------------------
    # Verify object actually exists
    # --------------------------------------------------------

    admin_db = get_admin_db()

    directory = "/".join(
        payload.path.split("/")[:-1]
    )

    filename = payload.path.split("/")[-1]

    storage_response = (
        admin_db.storage
        .from_(payload.bucket)
        .list(directory)
    )

    if storage_response is None:
        raise HTTPException(
            status_code=404,
            detail="Uploaded file not found",
        )

    file_exists = any(
        item.get("name") == filename
        for item in storage_response
    )

    if not file_exists:
        raise HTTPException(
            status_code=404,
            detail="Uploaded file does not exist in storage",
        )

    # --------------------------------------------------------
    # Create attachment record
    # --------------------------------------------------------

    response = (
        db
        .table("attachments")
        .insert({
            "meeting_id": payload.meeting_id,
            "uploaded_by": user_id,
            "file_name": payload.file_name,
            "file_path": payload.path,
            "file_type": payload.file_type,
            "file_size": payload.file_size,
            "attachment_type": payload.attachment_type,
        })
        .execute()
    )

    if not response.data:
        raise HTTPException(
            status_code=400,
            detail="Failed to create attachment",
        )

    return {
        "attachment": response.data[0],
        "message": "Upload confirmed",
    }