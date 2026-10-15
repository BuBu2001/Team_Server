"""Mermaid compiler — a line-by-line port of GitDiagram's `compileDiagramGraph`.

Keeping this identical means a graph produced by static analysis renders exactly
like one produced by the model, and can be pasted into the original front end.
Source: gitdiagram `src/server/generate/graph.ts`.
"""

from __future__ import annotations

import re
from typing import Optional

from .model import DiagramGraph, Edge, Node, normalize_text

CONTROL_RE = re.compile(r"[\u0000-\u001f\u007f-\u009f\u2028\u2029]+")

GENERIC_NODE_TYPES = {
    "app",
    "application",
    "component",
    "directory",
    "folder",
    "library",
    "module",
    "package",
    "project",
    "repo",
    "repository",
    "service",
    "system",
    "utility",
}
MAX_NODE_FILE_HINT_LENGTH = 18

TONE_CLASS_NAMES = (
    "toneBlue",
    "toneAmber",
    "toneMint",
    "toneRose",
    "toneIndigo",
    "toneTeal",
)

CLASS_DEFS = (
    "classDef toneNeutral fill:#f8fafc,stroke:#334155,stroke-width:1.5px,color:#0f172a",
    "classDef toneBlue fill:#dbeafe,stroke:#2563eb,stroke-width:1.5px,color:#172554",
    "classDef toneAmber fill:#fef3c7,stroke:#d97706,stroke-width:1.5px,color:#78350f",
    "classDef toneMint fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#14532d",
    "classDef toneRose fill:#ffe4e6,stroke:#e11d48,stroke-width:1.5px,color:#881337",
    "classDef toneIndigo fill:#e0e7ff,stroke:#4f46e5,stroke-width:1.5px,color:#312e81",
    "classDef toneTeal fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#134e4a",
)


def escape_mermaid_text(value: str) -> str:
    escaped = (
        normalize_text(value)
        .replace("&", "&amp;")
        .replace("#", "&#35;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("`", "&#96;")
        .replace("\\", "&#92;")
        .replace("|", "&#124;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
        .replace("(", "&#40;")
        .replace(")", "&#41;")
        .strip()
    )
    return escaped or "Unnamed"


def detail_for_node(node: Node) -> Optional[str]:
    node_type = (node.type or "").strip()
    if not node_type:
        return None
    normalized_type = node_type.lower()
    normalized_label = node.label.strip().lower()
    if (
        normalized_type in GENERIC_NODE_TYPES
        or normalized_type == normalized_label
        or normalized_type in normalized_label
        or normalized_label in normalized_type
        or len(node_type.split()) > 4
    ):
        return None
    return escape_mermaid_text(node_type)


def file_hint_for_node(node: Node) -> Optional[str]:
    path = (node.path or "").strip()
    if not path or path.endswith("/") or "." not in path:
        return None
    file_name = path.rsplit("/", 1)[-1].strip()
    if not file_name or len(file_name) > MAX_NODE_FILE_HINT_LENGTH:
        return None
    return f"[{escape_mermaid_text(file_name)}]"


def label_for_node(node: Node) -> str:
    primary = escape_mermaid_text(node.label)
    secondary = detail_for_node(node)
    hint = file_hint_for_node(node)
    parts = [part for part in (primary, secondary if secondary is not None else hint) if part]
    return "<br/>".join(parts)


def mermaid_node_id(node_id: str) -> str:
    return f"node_{node_id}"


def mermaid_group_id(group_id: str) -> str:
    return f"group_{group_id}"


def render_node(node: Node) -> str:
    label = label_for_node(node)
    shape = node.shape or "box"
    node_id = mermaid_node_id(node.id)
    if shape == "database":
        return f'{node_id}[("{label}")]'
    if shape == "circle":
        return f'{node_id}(("{label}"))'
    if shape == "hexagon":
        return f'{node_id}{{{{"{label}"}}}}'
    return f'{node_id}["{label}"]'


def render_edge(edge: Edge) -> str:
    connector = "-.->" if edge.style == "dashed" else "-->"
    source = mermaid_node_id(edge.src)
    target = mermaid_node_id(edge.dst)
    if not edge.label:
        return f"{source} {connector} {target}"
    return f'{source} {connector}|"{escape_mermaid_text(edge.label)}"| {target}'


def tone_class_for_node(node: Node, group_order: dict[str, int]) -> str:
    if node.groupId is not None and node.groupId in group_order:
        return TONE_CLASS_NAMES[group_order[node.groupId] % len(TONE_CLASS_NAMES)]
    words = f"{node.label} {node.type}".lower()
    if node.shape == "database" or re.search(
        r"database|storage|cache|postgres|sqlite|redis|clickhouse", words
    ):
        return "toneAmber"
    if re.search(r"queue|worker|background|scheduler|task", words):
        return "toneRose"
    if re.search(r"client|browser|user|frontend|view|screen|ui\b", words):
        return "toneBlue"
    if re.search(r"api|server|route|request|handler|webhook", words):
        return "toneMint"
    if not node.path or re.search(r"model|inference|provider|integration", words):
        return "toneIndigo"
    return "toneTeal"


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def build_github_url(
    path: str,
    username: str,
    repo: str,
    branch: str,
    path_type: Optional[str] = None,
) -> str:
    kind = path_type or ("blob" if "." in path and not path.endswith("/") else "tree")
    encoded = "/".join(_quote(part) for part in path.split("/"))
    return (
        f"https://github.com/{_quote(username)}/{_quote(repo)}/{kind}/"
        f"{_quote(branch)}/{encoded}"
    )


def compile_diagram_graph(
    graph: DiagramGraph,
    username: Optional[str] = None,
    repo: Optional[str] = None,
    branch: str = "main",
    path_types: Optional[dict[str, str]] = None,
) -> str:
    lines: list[str] = ["flowchart TD"]
    grouped_node_ids: set[str] = set()
    class_assignments: dict[str, list[str]] = {}
    group_order = {group.id: index for index, group in enumerate(graph.groups)}

    def push(node: Node, indent: str = "") -> None:
        lines.append(f"{indent}{render_node(node)}")
        class_name = tone_class_for_node(node, group_order)
        class_assignments.setdefault(class_name, []).append(node.id)

    for group in graph.groups:
        lines.append("")
        lines.append(
            f'subgraph {mermaid_group_id(group.id)}["{escape_mermaid_text(group.label)}"]'
        )
        for node in [n for n in graph.nodes if n.groupId == group.id]:
            push(node, "  ")
            grouped_node_ids.add(node.id)
        lines.append("end")

    ungrouped = [node for node in graph.nodes if node.id not in grouped_node_ids]
    if ungrouped:
        lines.append("")
        for node in ungrouped:
            push(node)

    if graph.edges:
        lines.append("")
        for edge in graph.edges:
            lines.append(render_edge(edge))

    nodes_with_paths = [node for node in graph.nodes if node.path]
    if nodes_with_paths and username and repo:
        lines.append("")
        for node in nodes_with_paths:
            url = build_github_url(
                node.path or "",
                username,
                repo,
                branch,
                (path_types or {}).get(node.path or ""),
            )
            lines.append(f'click {mermaid_node_id(node.id)} "{url}"')

    lines.append("")
    lines.extend(CLASS_DEFS)

    for class_name, node_ids in class_assignments.items():
        if not node_ids:
            continue
        rendered = ",".join(mermaid_node_id(node_id) for node_id in node_ids)
        lines.append(f"class {rendered} {class_name}")

    return "\n".join(lines).strip()
