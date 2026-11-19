from fastapi import APIRouter

from app.db.mongo import get_mongo_client


router = APIRouter(
    tags=["Health"],
)


@router.get("/health")
@router.get("/health/")
@router.get("/api/v1/health")
@router.get("/api/v1/health/")
async def health_check():
    return {
        "status": "ok",
        "service": "MOM Creator API",
    }


@router.get("/health/db")
@router.get("/api/v1/health/db")
async def database_health_check():
    try:
        get_mongo_client().admin.command("ping")
    except Exception as exc:
        return {
            "status": "error",
            "database": "mongodb",
            "detail": str(exc),
        }

    return {
        "status": "ok",
        "database": "mongodb",
    }
