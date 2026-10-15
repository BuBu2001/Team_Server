"""End-to-end test on the fixture repository."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from staticdiagram.artifact import build_artifact
from staticdiagram.ingest import load_local
from staticdiagram.model import validate_graph
from staticdiagram.pipeline import generate_from_repository

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "shop_api"


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="staticdiagram-test-"))
        cls.result = generate_from_repository(load_local(FIXTURE), out_dir=cls.tmp)

    def test_graph_is_valid_against_the_tree(self):
        issues = validate_graph(self.result.graph, self.result.repo.file_tree)
        self.assertEqual(issues, [])

    def test_expected_components_and_relations(self):
        labels = {node.label for node in self.result.graph.nodes}
        self.assertLessEqual({"API", "DB", "Domain", "Workers", "CLI"}, labels)
        self.assertIn("PostgreSQL", labels)
        self.assertIn("FastAPI client", labels)
        verbs = {edge.label for edge in self.result.graph.edges}
        self.assertIn("enqueues", verbs)
        self.assertIn("queries", verbs)

    def test_tests_are_excluded(self):
        for component in self.result.analysis.components.values():
            for path in component.files:
                self.assertNotIn("/tests/", f"/{path}/")

    def test_every_internal_edge_has_evidence(self):
        tree = self.result.repo.file_tree
        for edge in self.result.graph.edges:
            target = next(n for n in self.result.graph.nodes if n.id == edge.dst)
            if target.path:
                self.assertIn(edge.evidencePath, tree, edge)
                text = self.result.repo.read_text(edge.evidencePath) or ""
                self.assertIn("import", text)

    def test_outputs_written(self):
        names = {path.name for path in self.result.outputs.files}
        self.assertLessEqual(
            {"diagram.mmd", "diagram.svg", "README.md", "graph.json",
             "analysis.json", "index.html", "artifact.json"},
            names,
        )

    def test_html_is_self_contained(self):
        html = (self.result.outputs.html or self.tmp / "x/index.html").read_text()
        self.assertNotIn("cdn.", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn('rel="stylesheet"', html)

    def test_deterministic(self):
        second = generate_from_repository(load_local(FIXTURE), out_dir=self.tmp)
        self.assertEqual(second.mermaid, self.result.mermaid)
        self.assertEqual(second.svg, self.result.svg)

    def test_artifact_matches_gitdiagram_contract(self):
        payload = build_artifact(
            self.result.analysis,
            self.result.explanation,
            self.result.graph,
            self.result.mermaid,
        )
        expected_keys = {
            "version",
            "visibility",
            "username",
            "repo",
            "stargazerCount",
            "diagram",
            "explanation",
            "graph",
            "generatedAt",
            "usedOwnKey",
            "latestSessionSummary",
            "lastSuccessfulAt",
        }
        self.assertEqual(set(payload), expected_keys)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["latestSessionSummary"]["provider"], "static")
        self.assertEqual(payload["latestSessionSummary"]["finalCost"]["amountUsd"], 0)

    def test_report_numbers(self):
        coverage = self.result.analysis.coverage
        self.assertEqual(coverage.files_parsed, 19)
        self.assertEqual(coverage.files_skipped_tests, 1)
        self.assertGreater(coverage.imports_resolved, 20)
        self.assertEqual(coverage.imports_unresolved, sum(coverage.unresolved_names.values()))


if __name__ == "__main__":
    unittest.main()
