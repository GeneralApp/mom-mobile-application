from app.db.client import DataClient, get_admin_db, get_user_db
from app.db.query import APIError, APIResponse

# Routes annotate their database dependency as `Client`.
Client = DataClient

__all__ = [
    "APIError",
    "APIResponse",
    "Client",
    "DataClient",
    "get_admin_db",
    "get_user_db",
]
