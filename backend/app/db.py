from functools import lru_cache

from pymongo import MongoClient
from pymongo.collection import Collection

from app.config import get_settings


@lru_cache
def get_client() -> MongoClient:
    return MongoClient(get_settings().mongodb_uri, tz_aware=True)


def get_collection() -> Collection:
    settings = get_settings()
    return get_client()[settings.mongodb_db][settings.mongodb_collection]
