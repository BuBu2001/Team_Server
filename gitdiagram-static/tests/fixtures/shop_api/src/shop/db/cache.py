"""Redis caching helpers for catalogue reads."""

import json
import os

import redis

client = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


def cached_index():
    """Return the catalogue index, refreshing it when missing."""
    payload = client.get("catalogue:index")
    if payload:
        return json.loads(payload)
    return []
