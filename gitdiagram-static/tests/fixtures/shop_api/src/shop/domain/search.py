"""Catalogue search backed by the Redis index."""

from shop.db.cache import cached_index


def search_products(query: str):
    """Match products by name."""
    index = cached_index()
    return [item for item in index if query.lower() in item["name"].lower()]
