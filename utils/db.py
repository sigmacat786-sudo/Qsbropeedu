import os
from pymongo import MongoClient

_client = None


def get_db():
    """
    Returns the MongoDB database handle.
    Reads connection string from env var MONGO_URI.
    Set this in Render's Environment tab (never hardcode it in code/repo).
    """
    global _client
    mongo_uri = os.environ.get("MONGO_URI")
    if not mongo_uri:
        raise RuntimeError(
            "MONGO_URI environment variable is not set. "
            "Add it in Render → Environment → Environment Variables."
        )

    if _client is None:
        _client = MongoClient(mongo_uri)

    db_name = os.environ.get("MONGO_DB_NAME", "smartyms_quiz_system")
    return _client[db_name]
