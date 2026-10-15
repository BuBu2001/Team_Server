"""The graph contract.

Field names, limits and validation rules mirror GitDiagram's
`src/features/diagram/graph.ts` (`diagramGraphSchema`) so a graph produced here
can be stored, compiled and rendered by the original front end unchanged.

Everything in this module is deterministic: no model, no randomness.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional

# Parity with GitDiagram's MAX_GRAPH_* constants.
MAX_GROUPS = 10
MAX_NODES = 34
MAX_EDGES = 48
MAX_LABEL_LENGTH = 72
MAX_TYPE_LENGTH = 72
MAX_DESCRIPTION_LENGTH = 240
MAX_PATH_LENGTH = 512

SHAPES = ("box", "database", "queue", "document", "circle", "hexagon")
EDGE_STYLES = ("solid", "dashed")

NodeShape = Literal["box", "database", "queue", "document", "circle", "hexagon"]
EdgeStyle = Literal["solid", "dashed"]

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CONTROL_RE = re.compile(
    "[\u0000-\u001f\u007f-\u009f\u2028\u2029]+",
)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Port of `normalizeDiagramText`: NFC, control chars to spaces, collapse."""
    value = unicodedata.normalize("NFC", value)
    value = _CONTROL_RE.sub(" ", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def clamp_text(value: str, limit: int) -> str:
    text = normalize_text(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def make_id(value: str, taken: Optional[set[str]] = None) -> str:
    """Deterministic slug matching the schema's `^[a-z][a-z0-9_]*$`."""
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not slug or not re.match(r"^[a-z]", slug):
        slug = f"n_{slug}" if slug else "n_node"
    slug = slug[:48]
    if taken is None:
        return slug
    candidate = slug
    suffix = 2
    while candidate in taken:
        candidate = f"{slug}_{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


@dataclass(slots=True)
class Group:
    id: str
    label: str
    description: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "description": self.description}


@dataclass(slots=True)
class Node:
    id: str
    label: str
    type: str = "component"
    groupId: Optional[str] = None
    path: Optional[str] = None
    shape: Optional[NodeShape] = None
    description: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "description": self.description,
            "groupId": self.groupId,
            "path": self.path,
            "shape": self.shape,
        }


@dataclass(slots=True)
class Edge:
    # `from` is a Python keyword; the JSON contract keeps GitDiagram's names.
    src: str
    dst: str
    label: Optional[str] = None
    style: Optional[EdgeStyle] = None
    evidencePath: Optional[str] = None
    description: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.src,
            "to": self.dst,
            "label": self.label,
            "description": self.description,
            "style": self.style,
            "evidencePath": self.evidencePath,
        }


@dataclass(slots=True)
class DiagramGraph:
    groups: list[Group] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": [g.to_dict() for g in self.groups],
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    def node_ids(self) -> set[str]:
        return {node.id for node in self.nodes}

    def group_ids(self) -> set[str]:
        return {group.id for group in self.groups}

    def node_by_id(self, node_id: str) -> Optional[Node]:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def drop_isolated_nodes(self, keep: Iterable[str] = ()) -> int:
        """Remove nodes with no edges, except the ones explicitly kept."""
        kept = set(keep)
        connected = {edge.src for edge in self.edges} | {
            edge.dst for edge in self.edges
        }
        before = len(self.nodes)
        self.nodes = [
            node
            for node in self.nodes
            if node.id in connected or node.id in kept
        ]
        self.groups = [
            group
            for group in self.groups
            if any(node.groupId == group.id for node in self.nodes)
        ]
        return before - len(self.nodes)


@dataclass(slots=True)
class Issue:
    category: str
    path: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"{self.path}: {self.message}"


def validate_graph(graph: DiagramGraph, file_tree: set[str]) -> list[Issue]:
    """Port of `validateDiagramGraph`: the same checks, the same categories."""
    issues: list[Issue] = []
    group_ids: set[str] = set()
    node_ids: set[str] = set()

    if len(graph.groups) > MAX_GROUPS:
        issues.append(
            Issue("schema_validation", "groups", f"More than {MAX_GROUPS} groups.")
        )
    if not graph.nodes:
        issues.append(Issue("schema_validation", "nodes", "Graph has no nodes."))
    if len(graph.nodes) > MAX_NODES:
        issues.append(
            Issue("schema_validation", "nodes", f"More than {MAX_NODES} nodes.")
        )
    if len(graph.edges) > MAX_EDGES:
        issues.append(
            Issue("schema_validation", "edges", f"More than {MAX_EDGES} edges.")
        )

    for index, group in enumerate(graph.groups):
        if not _ID_RE.match(group.id):
            issues.append(
                Issue(
                    "schema_validation",
                    f"groups.{index}.id",
                    f'Group id "{group.id}" does not match ^[a-z][a-z0-9_]*$.',
                )
            )
        if group.id in group_ids:
            issues.append(
                Issue(
                    "duplicate_group_id",
                    f"groups.{index}.id",
                    f'Duplicate group id "{group.id}".',
                )
            )
        group_ids.add(group.id)
        if not normalize_text(group.label):
            issues.append(
                Issue(
                    "schema_validation",
                    f"groups.{index}.label",
                    "Group label must contain visible text.",
                )
            )

    for index, node in enumerate(graph.nodes):
        if not _ID_RE.match(node.id):
            issues.append(
                Issue(
                    "schema_validation",
                    f"nodes.{index}.id",
                    f'Node id "{node.id}" does not match ^[a-z][a-z0-9_]*$.',
                )
            )
        if node.id in node_ids:
            issues.append(
                Issue(
                    "duplicate_node_id",
                    f"nodes.{index}.id",
                    f'Duplicate node id "{node.id}".',
                )
            )
        node_ids.add(node.id)
        if node.groupId and node.groupId not in group_ids:
            issues.append(
                Issue(
                    "unknown_group_id",
                    f"nodes.{index}.groupId",
                    f'Unknown group id "{node.groupId}" for node "{node.id}".',
                )
            )
        if node.shape is not None and node.shape not in SHAPES:
            issues.append(
                Issue(
                    "schema_validation",
                    f"nodes.{index}.shape",
                    f'Unknown shape "{node.shape}".',
                )
            )
        if node.path and node.path not in file_tree:
            issues.append(
                Issue(
                    "missing_repository_path",
                    f"nodes.{index}.path",
                    f'Path "{node.path}" does not exist in the repository file tree.',
                )
            )
        if not normalize_text(node.label):
            issues.append(
                Issue(
                    "schema_validation",
                    f"nodes.{index}.label",
                    "Node label must contain visible text.",
                )
            )

    for index, edge in enumerate(graph.edges):
        if edge.src not in node_ids:
            issues.append(
                Issue(
                    "unknown_edge_source",
                    f"edges.{index}.from",
                    f'Unknown source node id "{edge.src}".',
                )
            )
        if edge.dst not in node_ids:
            issues.append(
                Issue(
                    "unknown_edge_target",
                    f"edges.{index}.to",
                    f'Unknown target node id "{edge.dst}".',
                )
            )
        if edge.style is not None and edge.style not in EDGE_STYLES:
            issues.append(
                Issue(
                    "schema_validation",
                    f"edges.{index}.style",
                    f'Unknown edge style "{edge.style}".',
                )
            )
        if edge.evidencePath and edge.evidencePath not in file_tree:
            issues.append(
                Issue(
                    "missing_evidence_path",
                    f"edges.{index}.evidencePath",
                    f'Evidence path "{edge.evidencePath}" does not exist in the '
                    "repository file tree.",
                )
            )

    return issues


def strip_unknown_paths(
    graph: DiagramGraph, file_tree: set[str]
) -> tuple[DiagramGraph, int, int]:
    """Port of `stripUnknownGraphPaths`: unresolvable paths are cosmetic."""
    stripped_paths = 0
    stripped_evidence = 0
    for node in graph.nodes:
        if node.path and node.path not in file_tree:
            node.path = None
            stripped_paths += 1
    for edge in graph.edges:
        if edge.evidencePath and edge.evidencePath not in file_tree:
            edge.evidencePath = None
            stripped_evidence += 1
    return graph, stripped_paths, stripped_evidence
