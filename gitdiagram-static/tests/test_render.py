"""Layout tests: the offline SVG renderer must produce a sane drawing."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from staticdiagram.ingest import load_local
from staticdiagram.pipeline import generate_from_repository
from staticdiagram.svgrender import layout, render_svg

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "shop_api"


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="staticdiagram-render-"))
        cls.result = generate_from_repository(
            load_local(FIXTURE), out_dir=cls.tmp, formats=["svg"]
        )
        cls.graph = cls.result.graph
        cls.laid = layout(cls.graph)

    def test_every_node_is_placed(self):
        self.assertEqual(
            {node.id for node in self.graph.nodes}, set(self.laid.placed)
        )

    def test_no_overlapping_boxes(self):
        boxes = [
            (item.x, item.y, item.width, item.height, node_id)
            for node_id, item in self.laid.placed.items()
        ]
        for index, first in enumerate(boxes):
            for second in boxes[index + 1 :]:
                overlaps = (
                    first[0] < second[0] + second[2]
                    and second[0] < first[0] + first[2]
                    and first[1] < second[1] + second[3]
                    and second[1] < first[1] + first[3]
                )
                self.assertFalse(overlaps, f"{first[4]} overlaps {second[4]}")

    def test_canvas_contains_every_box(self):
        for node_id, item in self.laid.placed.items():
            self.assertGreaterEqual(item.x, 0, node_id)
            self.assertGreaterEqual(item.y, 0, node_id)
            self.assertLessEqual(item.x + item.width, self.laid.width + 1, node_id)
            self.assertLessEqual(item.y + item.height, self.laid.height + 1, node_id)

    def test_svg_mentions_nodes(self):
        svg = self.result.svg
        for node in self.graph.nodes:
            self.assertIn(node.label, svg)
        self.assertIn("marker-end", svg)
        self.assertIn("</svg>", svg)

    def test_links_point_to_github(self):
        svg = render_svg(
            self.graph, username="example", repo="shop", branch="main",
            path_types=self.result.repo.path_types,
        )
        self.assertIn("https://github.com/example/shop/", svg)
        self.assertIn('target="_blank"', svg)


if __name__ == "__main__":
    unittest.main()
