"""Mermaid parity test against GitDiagram's `compileDiagramGraph`.

The expected string below was derived line-by-line from
gitdiagram `src/server/generate/graph.ts` (escape table, label composition,
shapes, tone classes, click links). If this test passes, a graph produced by
static analysis renders identically to one produced by the model.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from staticdiagram.mermaid import compile_diagram_graph, escape_mermaid_text
from staticdiagram.model import DiagramGraph, Edge, Group, Node

EXPECTED = """flowchart TD

subgraph group_api["API layer"]
  node_routes["Routes &amp; auth<br/>[routes.py]"]
end

node_db[("Postgres &#35;1<br/>database")]

node_routes -->|"reads/writes"| node_db
node_routes -.-> node_db

click node_routes "https://github.com/o/r/blob/main/src/api/routes.py"

classDef toneNeutral fill:#f8fafc,stroke:#334155,stroke-width:1.5px,color:#0f172a
classDef toneBlue fill:#dbeafe,stroke:#2563eb,stroke-width:1.5px,color:#172554
classDef toneAmber fill:#fef3c7,stroke:#d97706,stroke-width:1.5px,color:#78350f
classDef toneMint fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#14532d
classDef toneRose fill:#ffe4e6,stroke:#e11d48,stroke-width:1.5px,color:#881337
classDef toneIndigo fill:#e0e7ff,stroke:#4f46e5,stroke-width:1.5px,color:#312e81
classDef toneTeal fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#134e4a
class node_routes toneBlue
class node_db toneAmber"""


class MermaidParityTest(unittest.TestCase):
    def graph(self) -> DiagramGraph:
        return DiagramGraph(
            groups=[Group(id="api", label="API layer")],
            nodes=[
                Node(
                    id="routes",
                    label="Routes & auth",
                    type="routes",
                    groupId="api",
                    path="src/api/routes.py",
                    shape="box",
                ),
                Node(
                    id="db",
                    label="Postgres #1",
                    type="database",
                    groupId=None,
                    path=None,
                    shape="database",
                ),
            ],
            edges=[
                Edge(src="routes", dst="db", label="reads/writes", style="solid"),
                Edge(src="routes", dst="db", style="dashed"),
            ],
        )

    def test_exact_output(self):
        rendered = compile_diagram_graph(
            self.graph(), username="o", repo="r", branch="main",
            path_types={"src/api/routes.py": "blob"},
        )
        self.assertEqual(rendered, EXPECTED)

    def test_escaping_table(self):
        cases = {
            "`cmd`": "&#96;cmd&#96;",
            "a|b": "a&#124;b",
            "[x]": "&#91;x&#93;",
            "{y}": "&#123;y&#125;",
            "(z)": "&#40;z&#41;",
            "back\\slash": "back&#92;slash",
            "  spaced\tout  ": "spaced out",
            "": "Unnamed",
        }
        for raw, expected in cases.items():
            self.assertEqual(escape_mermaid_text(raw), expected, raw)


if __name__ == "__main__":
    unittest.main()
