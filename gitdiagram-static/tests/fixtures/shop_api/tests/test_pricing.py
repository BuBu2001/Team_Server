"""Pricing tests — must never appear on the diagram."""

from shop.domain.pricing import quote_total


def test_quote_total():
    assert quote_total("sku", 1) >= 0
