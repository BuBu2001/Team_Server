"""Stock levels and reservation."""

from shop.db.models import Product, Stock
from shop.db.session import get_session


def reserve_stock(sku: str, quantity: int) -> None:
    """Reserve units for a pending order."""
    with get_session() as session:
        stock = session.query(Stock).filter_by(sku=sku).one()
        stock.reserved += quantity


def list_products():
    """Return the visible catalogue."""
    return [product.to_dict() for product in Product.all()]
