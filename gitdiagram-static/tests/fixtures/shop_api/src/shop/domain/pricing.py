"""Pricing rules, discounts and tax calculation."""

from decimal import Decimal

from shop.db.models import Product

TAX = Decimal("0.2")


def quote_total(sku: str, quantity: int) -> Decimal:
    """Price a basket line including tax."""
    unit = _unit_price(sku)
    return (unit * quantity) * (1 + TAX)


def _unit_price(sku: str) -> Decimal:
    product = Product.lookup(sku)
    return Decimal(str(product.price))


def apply_discount(total: Decimal, code: str) -> Decimal:
    """Apply a promotional code."""
    return total * Decimal("0.9") if code else total
