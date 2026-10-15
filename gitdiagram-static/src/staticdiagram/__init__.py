"""staticdiagram — architecture diagrams from static analysis, no AI.

Public API:

    from staticdiagram import generate
    result = generate("pallets/flask")
    print(result.mermaid)
    result.outputs.html
"""

from __future__ import annotations

from .cli import __version__, main
from .depgraph import AnalyzeOptions, Analysis, Component, analyze
from .explain import Explanation, build_explanation
from .ingest import Repository, load, load_local, read_project_meta
from .mermaid import compile_diagram_graph
from .model import DiagramGraph, Edge, Group, Issue, Node, validate_graph
from .pipeline import Result, generate, generate_from_repository
from .svgrender import render_svg

__all__ = [
    "AnalyzeOptions",
    "Analysis",
    "Component",
    "DiagramGraph",
    "Edge",
    "Explanation",
    "Group",
    "Issue",
    "Node",
    "Repository",
    "Result",
    "__version__",
    "analyze",
    "build_explanation",
    "compile_diagram_graph",
    "generate",
    "generate_from_repository",
    "load",
    "load_local",
    "main",
    "read_project_meta",
    "render_svg",
    "validate_graph",
]
