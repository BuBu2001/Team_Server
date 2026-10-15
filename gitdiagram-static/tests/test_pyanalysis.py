"""Fact extraction tests: every claim on a diagram must trace to source."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from staticdiagram.pyanalysis import analyze_source

SAMPLE = '''
"""Order endpoints: checkout and status."""

import os
from fastapi import APIRouter, HTTPException
from celery import shared_task
from sqlalchemy.orm import declarative_base

import click

router = APIRouter()
Base = declarative_base()


class Order(Base):
    """A customer order."""

    __tablename__ = "orders"


@router.get("/{order_id}")
def read_order(order_id: str):
    """Return one order."""
    return {"id": order_id}


@shared_task
def notify(order_id: str) -> None:
    """Send a notification."""


@click.command()
def serve() -> None:
    """Run the server."""


API_KEY = os.environ.get("SHOP_API_KEY", "")

if __name__ == "__main__":
    serve()
'''


class PyAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.facts = analyze_source("pkg/orders.py", SAMPLE)

    def test_docstring_and_entrypoint(self):
        self.assertEqual(self.facts.docstring, "Order endpoints: checkout and status.")
        self.assertTrue(self.facts.entrypoint)

    def test_routes_tasks_and_commands_are_distinct(self):
        self.assertEqual(len(self.facts.routes), 1)
        self.assertEqual([name for name, _ in self.facts.tasks], ["notify"])
        self.assertEqual([name for name, _ in self.facts.commands], ["serve"])

    def test_signals(self):
        self.assertIn("web:fastapi", self.facts.signals)
        self.assertIn("queue:celery", self.facts.signals)
        self.assertIn("cli:click", self.facts.signals)
        self.assertIn("state:sql", self.facts.signals)
        self.assertIn("orm:model", self.facts.signals)

    def test_env_vars(self):
        self.assertEqual(self.facts.env_vars, {"SHOP_API_KEY"})

    def test_third_party(self):
        self.assertIn("fastapi", self.facts.third_party)
        self.assertNotIn("os", self.facts.third_party)

    def test_syntax_error_falls_back_to_regex(self):
        broken = analyze_source("pkg/broken.py", "from a.b import c\ndef (:\n")
        self.assertFalse(broken.parsed)
        self.assertTrue(any(item.module == "a.b" for item in broken.imports))


if __name__ == "__main__":
    unittest.main()
