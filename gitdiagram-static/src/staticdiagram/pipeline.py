"""End-to-end pipeline: repository in, diagram artefacts out."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import report
from .depgraph import AnalyzeOptions, Analysis, analyze
from .explain import Explanation, build_explanation
from .ingest import ProjectMeta, Repository, load, read_project_meta
from .mermaid import compile_diagram_graph
from .model import DiagramGraph, Issue, strip_unknown_paths, validate_graph
from .svgrender import render_svg

ALL_FORMATS = ("mermaid", "svg", "html", "markdown", "json", "facts", "artifact")


@dataclass
class Result:
    repo: Repository
    meta: ProjectMeta
    analysis: Analysis
    explanation: Explanation
    graph: DiagramGraph
    mermaid: str
    svg: str
    issues: list[Issue] = field(default_factory=list)
    stripped_paths: int = 0
    stripped_evidence: int = 0
    outputs: report.Outputs = field(default_factory=lambda: report.Outputs(Path(".")))


def _default_out_dir(repo: Repository, base: Optional[Path]) -> Path:
    slug = repo.slug.replace("/", "__") if repo.owner else repo.name
    return Path(base or Path("out")) / slug


def generate_from_repository(
    repo: Repository,
    options: Optional[AnalyzeOptions] = None,
    out_dir: Optional[Path] = None,
    formats: Iterable[str] = ALL_FORMATS,
    title: Optional[str] = None,
    png: bool = False,
) -> Result:
    options = options or AnalyzeOptions()
    meta = read_project_meta(repo)
    analysis = analyze(repo, meta, options)
    graph = analysis.graph or DiagramGraph()

    graph, stripped_paths, stripped_evidence = strip_unknown_paths(graph, repo.file_tree)
    issues = validate_graph(graph, repo.file_tree)
    analysis.graph = graph

    mermaid = compile_diagram_graph(
        graph,
        username=repo.owner,
        repo=repo.name,
        branch=repo.branch,
        path_types=repo.path_types,
    )
    explanation = build_explanation(analysis)
    heading = title or (meta.name or repo.slug)
    dot = "\u00b7"
    commit_bit = f" {dot} {repo.commit[:8]}" if repo.commit else ""
    subtitle = (
        f"{repo.slug} {dot} {repo.branch}{commit_bit} {dot} "
        f"{len(graph.nodes)} components, {len(graph.edges)} relations"
    )
    svg = render_svg(
        graph,
        title=heading,
        subtitle=subtitle,
        username=repo.owner,
        repo=repo.name,
        branch=repo.branch,
        path_types=repo.path_types,
    )

    destination = _default_out_dir(repo, out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    wanted = set(formats)
    outputs = report.Outputs(directory=destination)

    if not wanted or "mermaid" in wanted or "all" in wanted:
        outputs.mermaid = destination / "diagram.mmd"
        outputs.mermaid.write_text(mermaid + "\n", encoding="utf-8")
        outputs.files.append(outputs.mermaid)
    if not wanted or "svg" in wanted or "all" in wanted:
        outputs.svg = destination / "diagram.svg"
        outputs.svg.write_text(svg + "\n", encoding="utf-8")
        outputs.files.append(outputs.svg)
    if not wanted or "markdown" in wanted or "all" in wanted:
        outputs.markdown = report.write_markdown(
            analysis, explanation, mermaid, destination / "README.md", "diagram.svg"
        )
        outputs.files.append(outputs.markdown)
    if not wanted or "json" in wanted or "all" in wanted:
        outputs.graph_json = report.write_json(
            graph,
            destination / "graph.json",
            extra={
                "generator": "staticdiagram",
                "model": None,
                "repository": {
                    "owner": repo.owner,
                    "name": repo.name,
                    "branch": repo.branch,
                    "commit": repo.commit,
                },
                "explanation": explanation.overview,
            },
        )
        outputs.files.append(outputs.graph_json)
    if not wanted or "facts" in wanted or "all" in wanted:
        outputs.facts_json = report.write_facts(
            analysis, explanation, destination / "analysis.json"
        )
        outputs.files.append(outputs.facts_json)
    if not wanted or "artifact" in wanted or "all" in wanted:
        from .artifact import build_artifact, write_artifact

        payload = build_artifact(
            analysis,
            explanation,
            graph,
            mermaid,
            visibility="public" if repo.owner else "private",
        )
        outputs.repo_json = write_artifact(payload, destination / "artifact.json")
        outputs.files.append(outputs.repo_json)
    if not wanted or "html" in wanted or "all" in wanted:
        outputs.html = report.write_html(
            analysis, explanation, mermaid, svg, destination / "index.html", issues
        )
        outputs.files.append(outputs.html)

    if png and outputs.svg is not None:
        from .pngexport import render_png

        rendered = render_png(outputs.svg)
        if rendered is not None:
            outputs.files.append(rendered)

    if repo.cloned_into and not os.environ.get("STATICDIAGRAM_KEEP_CACHE"):
        shutil.rmtree(repo.cloned_into, ignore_errors=True)

    return Result(
        repo=repo,
        meta=meta,
        analysis=analysis,
        explanation=explanation,
        graph=graph,
        mermaid=mermaid,
        svg=svg,
        issues=issues,
        stripped_paths=stripped_paths,
        stripped_evidence=stripped_evidence,
        outputs=outputs,
    )


def generate(
    target: str,
    out_dir: Optional[Path] = None,
    options: Optional[AnalyzeOptions] = None,
    formats: Iterable[str] = ALL_FORMATS,
    token: Optional[str] = None,
    title: Optional[str] = None,
    png: bool = False,
) -> Result:
    repo = load(target, token=token or os.environ.get("GITHUB_TOKEN"))
    return generate_from_repository(
        repo, options=options, out_dir=out_dir, formats=formats, title=title, png=png
    )
