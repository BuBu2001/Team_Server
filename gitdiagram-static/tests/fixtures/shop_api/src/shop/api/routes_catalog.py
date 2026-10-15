"""Catalogue endpoints: product listing and search."""

from fastapi import APIRouter

from shop.db.session import get_session
from shop.domain.inventory import list_products
from shop.domain.search import search_products

router = APIRouter()


@router.get("/products")
def products():
    """List all products."""
    with get_session():
        return list_products()


@router.get("/search")
def search(query: str):
    """Search the catalogue."""
    return search_products(query)
