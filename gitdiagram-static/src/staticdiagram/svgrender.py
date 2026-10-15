"""Offline SVG rendering with a small layered (Sugiyama-style) layout.

The point of this module is that the diagram can be produced and viewed with no
network access and no Node toolchain: pure Python, no dependencies. Colours and
shapes follow the same tone classes GitDiagram's Mermaid compiler assigns, so
the two renderings of one graph look like the same product.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from .mermaid import build_github_url, tone_class_for_node
from .model import DiagramGraph, Node

TONES = {
    "toneNeutral": ("#f8fafc", "#334155", "#0f172a"),
    "toneBlue": ("#dbeafe", "#2563eb", "#172554"),
    "toneAmber": ("#fef3c7", "#d97706", "#78350f"),
    "toneMint": ("#dcfce7", "#16a34a", "#14532d"),
    "toneRose": ("#ffe4e6", "#e11d48", "#881337"),
    "toneIndigo": ("#e0e7ff", "#4f46e5", "#312e81"),
    "toneTeal": ("#ccfbf1", "#0f766e", "#134e4a"),
}

FONT = "'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
CHAR_WIDTH = 7.0
SECOND_CHAR_WIDTH = 6.0
MIN_NODE_WIDTH = 132
MAX_NODE_WIDTH = 250
NODE_HEIGHT = 52
RANK_GAP = 96
NODE_GAP = 34
GROUP_PAD = 20
GROUP_LABEL_HEIGHT = 22


@dataclass
class Placed:
    node: Node
    x: float = 0.0
    y: float = 0.0
    width: float = MIN_NODE_WIDTH
    height: float = NODE_HEIGHT
    rank: int = 0
    order: float = 0.0
    tone: str = "toneNeutral"
    second: str = ""
    url: Optional[str] = None

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def cy(self) -> float:
        return self.y + self.height / 2


@dataclass
class Layout:
    placed: dict[str, Placed] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    groups: list[tuple[str, str, tuple[float, float, float, float]]] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0


def _text_width(text: str, per_char: float) -> float:
    return len(text) * per_char


def _node_size(node: Node) -> tuple[float, float, str]:
    second = ""
    if node.type and node.type.strip().lower() != node.label.strip().lower():
        second = node.type.strip()
    elif node.path:
        second = node.path.rsplit("/", 1)[-1]
    if len(second) > 30:
        second = second[:29] + "\u2026"
    width = max(
        MIN_NODE_WIDTH,
        min(MAX_NODE_WIDTH, max(_text_width(node.label, CHAR_WIDTH), _text_width(second, SECOND_CHAR_WIDTH)) + 30),
    )
    height = NODE_HEIGHT + (14 if second else 0) - 8
    return width, height, second


def _break_cycles(node_ids: list[str], edges: list[tuple[str, str]]) -> set[tuple[str, str]]:
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for src, dst in edges:
        if src in adjacency and dst in adjacency:
            adjacency[src].append(dst)
    state = {node_id: 0 for node_id in node_ids}
    back: set[tuple[str, str]] = set()

    def visit(current: str) -> None:
        state[current] = 1
        for neighbour in adjacency[current]:
            if state[neighbour] == 1:
                back.add((current, neighbour))
            elif state[neighbour] == 0:
                visit(neighbour)
        state[current] = 2

    for node_id in node_ids:
        if state[node_id] == 0:
            visit(node_id)
    return back


def layout(graph: DiagramGraph, title: Optional[str] = None) -> Layout:
    result = Layout()
    if not graph.nodes:
        result.width, result.height = 320, 160
        return result

    node_ids = [node.id for node in graph.nodes]
    pairs = [(edge.src, edge.dst) for edge in graph.edges if edge.src in set(node_ids) and edge.dst in set(node_ids)]
    back_edges = _break_cycles(node_ids, pairs)
    forward = [pair for pair in pairs if pair not in back_edges]

    incoming: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for src, dst in forward:
        incoming[dst].append(src)
        outgoing[src].append(dst)

    # --- ranks: longest path from a source --------------------------------
    ranks = {node_id: 0 for node_id in node_ids}
    for _ in range(len(node_ids) + 2):
        changed = False
        for src, dst in forward:
            if ranks[dst] < ranks[src] + 1:
                ranks[dst] = ranks[src] + 1
                changed = True
        if not changed:
            break

    degree = {
        node_id: len(incoming[node_id]) + len(outgoing[node_id]) for node_id in node_ids
    }
    group_order = {group.id: index for index, group in enumerate(graph.groups)}
    nodes_by_id = {node.id: node for node in graph.nodes}

    buckets: dict[int, list[str]] = {}
    for node_id in node_ids:
        buckets.setdefault(ranks[node_id], []).append(node_id)
    for rank, members in buckets.items():
        members.sort(
            key=lambda node_id: (
                group_order.get(nodes_by_id[node_id].groupId or "", 99),
                -degree[node_id],
                node_id,
            )
        )

    # --- barycenter ordering sweeps ---------------------------------------
    ordered_ranks = sorted(buckets)
    for iteration in range(6):
        downward = iteration % 2 == 0
        sequence = ordered_ranks if downward else list(reversed(ordered_ranks))
        for rank in sequence:
            neighbours = (
                buckets.get(rank - 1, []) if downward else buckets.get(rank + 1, [])
            )
            if not neighbours:
                continue
            positions = {node_id: index for index, node_id in enumerate(neighbours)}

            def key(node_id: str) -> tuple[float, float, str]:
                links = incoming[node_id] if downward else outgoing[node_id]
                values = [positions[link] for link in links if link in positions]
                barycenter = sum(values) / len(values) if values else buckets[rank].index(node_id)
                return (
                    barycenter,
                    group_order.get(nodes_by_id[node_id].groupId or "", 99),
                    node_id,
                )

            buckets[rank] = sorted(buckets[rank], key=key)

    # --- sizes and initial x ----------------------------------------------
    placed: dict[str, Placed] = {}
    for node in graph.nodes:
        width, height, second = _node_size(node)
        placed[node.id] = Placed(
            node=node,
            width=width,
            height=height,
            rank=ranks[node.id],
            second=second,
            tone=tone_class_for_node(node, group_order),
        )

    for rank in ordered_ranks:
        cursor = 0.0
        for node_id in buckets[rank]:
            item = placed[node_id]
            item.x = cursor
            item.y = rank * (NODE_HEIGHT + RANK_GAP)
            cursor += item.width + NODE_GAP

    # --- refine x toward neighbour barycenters ----------------------------
    for _ in range(8):
        for rank in ordered_ranks:
            members = buckets[rank]
            desired: dict[str, float] = {}
            for node_id in members:
                links = [
                    placed[other].cx
                    for other in incoming[node_id] + outgoing[node_id]
                    if other in placed
                ]
                desired[node_id] = sum(links) / len(links) if links else placed[node_id].cx
            ordered = sorted(members, key=lambda node_id: (desired[node_id], node_id))
            cursor = -math.inf
            for node_id in ordered:
                item = placed[node_id]
                target = desired[node_id] - item.width / 2
                item.x = max(target, cursor + NODE_GAP) if cursor != -math.inf else target
                cursor = item.x + item.width

    # --- normalise coordinates --------------------------------------------
    min_x = min(item.x for item in placed.values())
    min_y = min(item.y for item in placed.values())
    for item in placed.values():
        item.x -= min_x - GROUP_PAD * 2
        item.y -= min_y - GROUP_PAD * 2 - (GROUP_LABEL_HEIGHT if title else 0)

    # --- group boxes -------------------------------------------------------
    groups: list[tuple[str, str, tuple[float, float, float, float]]] = []
    for group in graph.groups:
        members = [placed[node.id] for node in graph.nodes if node.groupId == group.id and node.id in placed]
        if not members:
            continue
        left = min(item.x for item in members) - GROUP_PAD
        top = min(item.y for item in members) - GROUP_PAD - GROUP_LABEL_HEIGHT
        right = max(item.x + item.width for item in members) + GROUP_PAD
        bottom = max(item.y + item.height for item in members) + GROUP_PAD
        groups.append((group.id, group.label, (left, top, right - left, bottom - top)))

    width = max(item.x + item.width for item in placed.values()) + GROUP_PAD * 2
    height = max(item.y + item.height for item in placed.values()) + GROUP_PAD * 2
    for _, _, (left, top, box_width, box_height) in groups:
        width = max(width, left + box_width + GROUP_PAD)
        height = max(height, top + box_height + GROUP_PAD)

    result.placed = placed
    result.order = [node_id for rank in ordered_ranks for node_id in buckets[rank]]
    result.groups = groups
    result.width = width
    result.height = height
    return result


def _shape_svg(item: Placed) -> str:
    fill, stroke, text = TONES.get(item.tone, TONES["toneNeutral"])
    x, y, width, height = item.x, item.y, item.width, item.height
    shape = item.node.shape or "box"
    style = f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"'
    if shape == "circle":
        return f'<ellipse cx="{x + width / 2:.1f}" cy="{y + height / 2:.1f}" rx="{width / 2:.1f}" ry="{height / 2:.1f}" {style}/>'
    if shape == "database":
        ry = 9.0
        body = (
            f'<path d="M{x:.1f} {y + ry:.1f} v{height - 2 * ry:.1f} '
            f'a{width / 2:.1f} {ry:.1f} 0 0 0 {width:.1f} 0 v-{height - 2 * ry:.1f}" {style}/>'
        )
        lid = f'<ellipse cx="{x + width / 2:.1f}" cy="{y + ry:.1f}" rx="{width / 2:.1f}" ry="{ry:.1f}" {style}/>'
        return body + lid
    if shape == "hexagon":
        inset = min(18.0, width * 0.12)
        points = [
            (x + inset, y),
            (x + width - inset, y),
            (x + width, y + height / 2),
            (x + width - inset, y + height),
            (x + inset, y + height),
            (x, y + height / 2),
        ]
        rendered = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        return f'<polygon points="{rendered}" {style}/>'
    radius = 10 if shape in {"queue", "document"} else 8
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" rx="{radius}" {style}/>'


def _label_svg(item: Placed) -> str:
    _, _, text_color = TONES.get(item.tone, TONES["toneNeutral"])
    cx = item.x + item.width / 2
    label = xml_escape(item.node.label)
    parts = [
        f'<text x="{cx:.1f}" y="{item.cy - (4 if item.second else -4):.1f}" text-anchor="middle" '
        f'font-family="{FONT}" font-size="13" font-weight="600" fill="{text_color}">{label}</text>'
    ]
    if item.second:
        parts.append(
            f'<text x="{cx:.1f}" y="{item.cy + 13:.1f}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="10.5" fill="{text_color}" opacity="0.78">'
            f"{xml_escape(item.second)}</text>"
        )
    return "".join(parts)


def _bezier_point(
    t: float,
    start: tuple[float, float],
    c1: tuple[float, float],
    c2: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float]:
    u = 1 - t
    x = u**3 * start[0] + 3 * u**2 * t * c1[0] + 3 * u * t**2 * c2[0] + t**3 * end[0]
    y = u**3 * start[1] + 3 * u**2 * t * c1[1] + 3 * u * t**2 * c2[1] + t**3 * end[1]
    return x, y


def _edge_geometry(
    source: Placed, target: Placed
) -> tuple[str, tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]:
    if target.y > source.y + source.height - 4:
        start = (source.cx, source.y + source.height)
        end = (target.cx, target.y)
    elif source.y > target.y + target.height - 4:
        start = (source.cx, source.y)
        end = (target.cx, target.y + target.height)
    elif target.cx >= source.cx:
        start = (source.x + source.width, source.cy)
        end = (target.x, target.cy)
    else:
        start = (source.x, source.cy)
        end = (target.x + target.width, target.cy)

    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if abs(dy) >= abs(dx):
        control = max(28.0, abs(dy) * 0.45)
        c1 = (start[0], start[1] + math.copysign(control, dy))
        c2 = (end[0], end[1] - math.copysign(control, dy))
    else:
        control = max(24.0, abs(dx) * 0.4)
        c1 = (start[0] + math.copysign(control, dx), start[1])
        c2 = (end[0] - math.copysign(control, dx), end[1])
    path = (
        f"M{start[0]:.1f} {start[1]:.1f} C{c1[0]:.1f} {c1[1]:.1f}, "
        f"{c2[0]:.1f} {c2[1]:.1f}, {end[0]:.1f} {end[1]:.1f}"
    )
    return path, start, c1, c2, end


def render_svg(
    graph: DiagramGraph,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    username: Optional[str] = None,
    repo: Optional[str] = None,
    branch: str = "main",
    path_types: Optional[dict[str, str]] = None,
) -> str:
    laid_out = layout(graph)
    header = 0.0
    if title:
        header = 44 + (18 if subtitle else 0)
    width = max(laid_out.width, 640)
    height = laid_out.height + header

    for item in laid_out.placed.values():
        item.y += header
        if item.node.path and username and repo:
            item.url = build_github_url(
                item.node.path, username, repo, branch, (path_types or {}).get(item.node.path)
            )
    groups = [
        (group_id, label, (left, top + header, box_width, box_height))
        for group_id, label, (left, top, box_width, box_height) in laid_out.groups
    ]

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="{FONT}">',
        "<defs>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#64748b"/></marker>',
        '<marker id="arrowDash" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#94a3b8"/></marker>',
        "</defs>",
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="#ffffff"/>',
    ]

    if title:
        parts.append(
            f'<text x="24" y="30" font-size="17" font-weight="700" fill="#0f172a">{xml_escape(title)}</text>'
        )
        if subtitle:
            parts.append(
                f'<text x="24" y="50" font-size="11.5" fill="#64748b">{xml_escape(subtitle)}</text>'
            )

    group_tones = ["#f1f5f9", "#fefce8", "#f0fdf4", "#fdf2f8", "#eef2ff", "#ecfeff"]
    group_strokes = ["#cbd5e1", "#fde68a", "#bbf7d0", "#fbcfe8", "#c7d2fe", "#a5f3fc"]
    for index, (_, label, (left, top, box_width, box_height)) in enumerate(groups):
        tone = group_tones[index % len(group_tones)]
        stroke = group_strokes[index % len(group_strokes)]
        parts.append(
            f'<rect x="{left:.1f}" y="{top:.1f}" width="{box_width:.1f}" height="{box_height:.1f}" '
            f'rx="14" fill="{tone}" stroke="{stroke}" stroke-width="1.2" stroke-dasharray="5 4"/>'
        )
        parts.append(
            f'<text x="{left + 12:.1f}" y="{top + 17:.1f}" font-size="11.5" font-weight="700" '
            f'fill="#475569" letter-spacing="0.4">{xml_escape(label.upper())}</text>'
        )

    node_rects = [
        (item.x - 2, item.y - 2, item.width + 4, item.height + 4)
        for item in laid_out.placed.values()
    ]
    edge_labels: list[tuple[float, float, str]] = []
    for edge in graph.edges:
        source = laid_out.placed.get(edge.src)
        target = laid_out.placed.get(edge.dst)
        if not source or not target:
            continue
        path, start, c1, c2, end = _edge_geometry(source, target)
        dashed = edge.style == "dashed"
        color = "#94a3b8" if dashed else "#64748b"
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        marker = "arrowDash" if dashed else "arrow"
        parts.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.5"'
            f'{dash} marker-end="url(#{marker})"/>'
        )
        if not edge.label:
            continue
        width = _text_width(edge.label, 5.9) + 8
        placed_label: tuple[float, float] | None = None
        for t in (0.5, 0.44, 0.56, 0.38, 0.62, 0.32, 0.68, 0.26, 0.74):
            point = _bezier_point(t, start, c1, c2, end)
            rect = (point[0] - width / 2, point[1] - 8, width, 15)
            if any(
                rect[0] < other[0] + other[2]
                and other[0] < rect[0] + rect[2]
                and rect[1] < other[1] + other[3]
                and other[1] < rect[1] + rect[3]
                for other in node_rects + [
                    (lx - width / 2, ly - 8, width, 15) for lx, ly, _ in edge_labels
                ]
            ):
                continue
            placed_label = point
            break
        if placed_label is not None:
            edge_labels.append((placed_label[0], placed_label[1], edge.label))

    for node_id in laid_out.order:
        item = laid_out.placed[node_id]
        body = _shape_svg(item) + _label_svg(item)
        if item.url:
            href = xml_escape(item.url, {'"': "&quot;"})
            parts.append(
                f'<a href="{href}" target="_blank" '
                f'rel="noopener"><g class="node">{body}</g></a>'
            )
        else:
            parts.append(f'<g class="node">{body}</g>')

    # Labels that found a free spot are drawn last; a verb never hides a name.
    for label_x, label_y, label in edge_labels:
        text = xml_escape(label)
        label_width = _text_width(label, 5.9) + 8
        parts.append(
            f'<rect x="{label_x - label_width / 2:.1f}" y="{label_y - 8:.1f}" '
            f'width="{label_width:.1f}" height="15" rx="7" fill="#ffffff" opacity="0.92"/>'
        )
        parts.append(
            f'<text x="{label_x:.1f}" y="{label_y + 3.5:.1f}" text-anchor="middle" '
            f'font-size="10" fill="#475569">{text}</text>'
        )

    parts.append(
        f'<text x="{width - 16:.0f}" y="{height - 10:.0f}" text-anchor="end" font-size="10" '
        f'fill="#94a3b8">generated by staticdiagram \u00b7 no AI</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)
