"""Operator command line interface."""

import click

from shop.db.session import init_db
from shop.domain.pricing import quote_total


@click.group()
def main() -> None:
    """Shop operator tools."""


@main.command()
def migrate() -> None:
    """Create database tables."""
    init_db()


@main.command()
@click.argument("sku")
def price(sku: str) -> None:
    """Print the total for one unit."""
    click.echo(quote_total(sku, 1))


if __name__ == "__main__":
    main()
