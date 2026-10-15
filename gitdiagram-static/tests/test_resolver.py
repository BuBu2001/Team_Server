"""Resolver tests: imports must resolve inside a real layout."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from staticdiagram.ingest import load_local
from staticdiagram.pyanalysis import analyze_source
from staticdiagram.resolver import build_index, resolve_import

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "shop_api"


class ResolverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = load_local(FIXTURE)
        cls.index = build_index(cls.repo, ["src"])

    def _facts(self, path: str):
        return analyze_source(path, self.repo.read_text(path))

    def _import_of(self, path: str, module: str):
        for item in self._facts(path).imports:
            if item.module == module:
                return item
        self.fail(f"no import of {module} in {path}")

    def test_absolute_import_resolves(self):
        item = self._import_of("src/shop/api/routes_orders.py", "shop.domain.inventory")
        self.assertEqual(
            resolve_import(self.index, item, "src/shop/api/routes_orders.py"),
            "src/shop/domain/inventory.py",
        )

    def test_third_party_is_not_in_repo(self):
        item = self._import_of("src/shop/db/cache.py", "redis")
        self.assertIsNone(resolve_import(self.index, item, "src/shop/db/cache.py"))

    def test_relative_import_within_package(self):
        item = self._import_of("src/shop/domain/search.py", "shop.db.cache")
        target = resolve_import(self.index, item, "src/shop/domain/search.py")
        self.assertEqual(target, "src/shop/db/cache.py")

    def test_import_of_package_resolves_to_init(self):
        item = self._import_of("src/shop/api/routes_orders.py", "shop.db.session")
        self.assertEqual(
            resolve_import(self.index, item, "src/shop/api/routes_orders.py"),
            "src/shop/db/session.py",
        )


if __name__ == "__main__":
    unittest.main()
