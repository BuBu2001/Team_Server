"""The deterministic replacement for the model's prose.

GitDiagram streams an `explanation` string written by an LLM. Here the same field
is assembled from facts the analyser actually verified: README text, packaging
metadata, entry points, the ranked dependency graph, and the drivers found in
imports. Nothing is invented — if a claim cannot be backed by a file, it is not
made, and the coverage section says what was not checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .depgraph import Analysis, Component
from .ingest import ProjectMeta

BADGE = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)|!\[[^\]]*\]\([^)]*\)|<[^>]+>")
HEADING = re.compile(r"^#{1,6}\s+(.*)$")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def readme_title(readme: str) -> Optional[str]:
    for line in readme.splitlines():
        match = HEADING.match(line.strip())
        if match:
            return match.group(1).strip()
    return None


def readme_summary(readme: str, limit: int = 420) -> str:
    """First prose paragraph, with badges, images and HTML stripped."""
    text = BADGE.sub(" ", readme)
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        cleaned = block.strip()
        if not cleaned:
            continue
        if cleaned.startswith(("```", "---", "===", "|", ">", "#", "![", "<p")):
            continue
        if re.match(r"^\s*\[?!\[", cleaned):
            continue
        cleaned = LINK.sub(r"\1", cleaned)
        cleaned = re.sub(r"[*_`]+", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) < 25:
            continue
        paragraphs.append(cleaned)
        if len(" ".join(paragraphs)) > limit:
            break
    joined = " ".join(paragraphs)
    if len(joined) > limit:
        joined = joined[:limit].rsplit(" ", 1)[0] + "\u2026"
    return joined


@dataclass
class Explanation:
    overview: str
    sections: list[tuple[str, str]] = field(default_factory=list)
    facts: dict = field(default_factory=dict)


def _plural(count: int, one: str, many: Optional[str] = None) -> str:
    return one if count == 1 else (many or f"{one}s")


def _path(component: Component) -> str:
    return f"`{component.path}`"


def build_explanation(analysis: Analysis) -> Explanation:
    repo = analysis.repo
    meta = analysis.meta
    components = analysis.component_list()
    shown = analysis.selected()
    graph = analysis.graph
    coverage = analysis.coverage

    total_loc = sum(component.loc for component in components)
    groups = graph.groups if graph else []
    from .depgraph import entry_rank

    entry_components = sorted(
        (component for component in components if component.entrypoints), key=entry_rank
    )
    route_components = [component for component in components if component.routes]
    task_components = [component for component in components if component.tasks]
    inbound_ranked = sorted(components, key=lambda item: -item.inbound)

    purpose = (
        meta.description
        or readme_title(repo.readme)
        or readme_summary(repo.readme, 160)
        or f"{repo.name}"
    )
    summary = readme_summary(repo.readme, 220)

    sentences: list[str] = []
    sentences.append(
        f"{repo.name} is a Python codebase of {coverage.files_parsed} parsed "
        f"{_plural(coverage.files_parsed, 'module')}"
        f"{f' ({total_loc:,} lines)' if total_loc else ''}"
        f"{f' packaging as `{meta.name}`' if meta.name and meta.name != repo.name else ''}."
    )
    if summary:
        sentences.append(summary if summary.endswith(".") else summary + ".")
    if groups:
        rendered = ", ".join(
            f"{group.label} ({sum(1 for node in (graph.nodes if graph else []) if node.groupId == group.id)})"
            for group in groups[:5]
        )
        sentences.append(
            f"The map splits into {len(groups)} "
            f"{_plural(len(groups), 'subsystem', 'subsystems')}: {rendered}."
        )
    else:
        rendered = ", ".join(f"{component.label}" for component in shown[:6])
        sentences.append(
            f"Its {len(shown)} main components are {rendered}."
        )
    if route_components:
        top = max(route_components, key=lambda item: item.routes)
        sentences.append(
            f"HTTP requests are handled in {_path(top)} "
            f"({top.routes} route handler{_plural(top.routes, '', 's')})."
        )
    console = next(iter(meta.console_scripts), None)
    if entry_components and (console or route_components or task_components):
        first = entry_components[0]
        detail = first.entrypoints[0]
        where = (
            f"Execution starts at `{detail}`"
            if detail == first.path
            else f"Execution starts in {_path(first)} at `{detail}`"
        )
        sentences.append(where + (f", exposed as the `{console}` command." if console else "."))
    elif entry_components:
        rendered = ", ".join(
            f"`{component.entrypoints[0]}`" for component in entry_components[:3]
        )
        sentences.append(
            f"It is a library: the only runnable guards are {rendered}."
        )
    if inbound_ranked and inbound_ranked[0].inbound_from:
        core = inbound_ranked[0]
        sentences.append(
            f"The most depended-on module is {_path(core)}, imported by "
            f"{core.inbound_from} other component{_plural(core.inbound_from, '', 's')} "
            f"({core.inbound} import statement{_plural(core.inbound, '', 's')})."
        )
    if analysis.externals:
        rendered = ", ".join(external.label for external in analysis.externals[:4])
        sentences.append(f"Imports and infrastructure show external systems: {rendered}.")
    sentences.append(
        f"Built from {coverage.imports_resolved} resolved internal imports; "
        f"no model was called."
    )

    overview = " ".join(sentences)

    sections: list[tuple[str, str]] = []
    sections.append(("Overview", overview))

    if shown:
        rows = [
            "| Component | Path | Files | Lines | Used by | Uses | Signals |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for component in shown:
            signals = ", ".join(sorted(component.signals)[:4]) or "\u2014"
            rows.append(
                f"| {component.label} | `{component.path}` | {len(component.files)} "
                f"| {component.loc} | {component.inbound_from} | {component.outbound_to} "
                f"| {signals} |"
            )
        sections.append(("Components", "\n".join(rows)))

    if graph and graph.edges:
        rows = ["| From | Relation | To | Evidence |", "| --- | --- | --- | --- |"]
        node_by_id = {node.id: node for node in graph.nodes}
        for edge in graph.edges:
            source = node_by_id.get(edge.src)
            target = node_by_id.get(edge.dst)
            if not source or not target:
                continue
            evidence = (
                f"`{edge.evidencePath}`"
                if edge.evidencePath
                else ("external" if not target.path else "\u2014")
            )
            rows.append(
                f"| {source.label} | {edge.label or 'relates to'} | {target.label} | {evidence} |"
            )
        sections.append(("Connections", "\n".join(rows)))

    entry_lines = []
    for component in entry_components[:8]:
        for entry in component.entrypoints[:3]:
            entry_lines.append(f"- `{entry}` \u2014 part of **{component.label}** (`{component.path}`)")
    for component in components:
        if component.commands:
            entry_lines.append(
                f"- CLI commands in `{component.path}` \u2014 {component.commands} command"
                f"{_plural(component.commands, '', 's')}"
            )
    for script, target in list(meta.console_scripts.items())[:8]:
        entry_lines.append(f"- console script `{script}` \u2192 `{target}`")
    for component in route_components[:8]:
        entry_lines.append(
            f"- HTTP routes in `{component.path}` \u2014 {component.routes} handler"
            f"{_plural(component.routes, '', 's')}"
        )
    for component in task_components[:8]:
        entry_lines.append(
            f"- background tasks in `{component.path}` \u2014 {component.tasks} task"
            f"{_plural(component.tasks, '', 's')}"
        )
    if entry_lines:
        sections.append(("Entry points", "\n".join(dict.fromkeys(entry_lines))))

    external_lines = []
    for external in analysis.externals:
        users = ", ".join(f"`{path}`" for path, _ in external.users.most_common(3))
        proof = next(iter(external.evidence), None)
        external_lines.append(
            f"- **{external.label}** \u2014 used by {users}"
            + (f" (seen in `{proof}`)" if proof else "")
        )
    infra = analysis.infra or {}
    for service in infra.get("system_services", [])[:8]:
        external_lines.append(
            f"- **{service['system']}** \u2014 service `{service['name']}` in `{service['evidence']}`"
        )
    if external_lines:
        sections.append(("State and external systems", "\n".join(external_lines)))

    dependencies = meta.top_dependencies(18)
    if dependencies:
        sections.append(
            (
                "Runtime dependencies",
                ", ".join(f"`{item}`" for item in dependencies)
                + (f" (of {len(meta.dependencies)} declared)" if len(meta.dependencies) > 18 else ""),
            )
        )

    infra_lines = []
    for container in infra.get("dockerfiles", [])[:3]:
        base = container["base"][0] if container["base"] else "unknown base"
        entry = container["entrypoint"][0] if container["entrypoint"] else None
        infra_lines.append(
            f"- `{container['path']}` \u2014 {base}" + (f", runs {entry}" if entry else "")
        )
    for service in infra.get("services", [])[:10]:
        infra_lines.append(
            f"- compose service `{service['name']}`"
            + (f" = `{service['image']}`" if service["image"] else "")
            + f" ({service['evidence']})"
        )
    for workflow in infra.get("workflows", [])[:5]:
        if workflow["jobs"]:
            infra_lines.append(
                f"- CI `{workflow['path']}` \u2014 jobs: {', '.join(workflow['jobs'][:6])}"
            )
    makefile = infra.get("makefile")
    if makefile and makefile["targets"]:
        infra_lines.append(
            f"- `{makefile['path']}` targets: {', '.join(makefile['targets'][:12])}"
        )
    env = infra.get("env_example")
    if env and env["keys"]:
        infra_lines.append(
            f"- `{env['path']}` declares {len(env['keys'])} settings: "
            + ", ".join(f"`{key}`" for key in env["keys"][:12])
        )
    if infra_lines:
        sections.append(("Deployment and operations", "\n".join(infra_lines)))

    limits = [
        f"Parsed {coverage.files_parsed} of {coverage.files_total} repository files; "
        f"{coverage.files_skipped_tests} skipped as tests/examples/docs, "
        f"{coverage.files_skipped_other} skipped as vendored, sensitive or oversized.",
        f"{coverage.imports_resolved} imports resolved to repository paths, "
        f"{coverage.imports_unresolved} point outside the repository "
        f"(stdlib or third-party), {coverage.imports_stdlib} were stdlib.",
        f"{coverage.components_total} components detected at directory depth "
        f"{coverage.depth}; {coverage.components_shown} shown on the diagram.",
    ]
    if coverage.files_failed:
        limits.append(
            f"{coverage.files_failed} file(s) failed to parse and were read with a "
            "regex fallback: "
            + ", ".join(f"`{path}`" for path, _ in coverage.syntax_errors[:5])
        )
    top_unresolved = [name for name, _ in coverage.unresolved_names.most_common(8)]
    if top_unresolved:
        limits.append(
            "Top unresolved import roots (treated as external): "
            + ", ".join(f"`{name}`" for name in top_unresolved)
            + "."
        )
    limits.append(
        "Only static imports are traced. Wiring through configuration, HTTP calls, "
        "message brokers, plugin registries or `globals()`-style dynamic imports is "
        "not detected, and neither is runtime behaviour."
    )
    sections.append(("Coverage and limits", "\n".join(f"- {line}" for line in limits)))

    facts = {
        "purpose": purpose,
        "readme_title": readme_title(repo.readme),
        "total_loc": total_loc,
        "groups": [group.label for group in groups],
        "entry_components": [component.path for component in entry_components],
        "route_components": [component.path for component in route_components],
        "task_components": [component.path for component in task_components],
        "core_modules": [
            {"path": component.path, "inbound": component.inbound, "loc": component.loc}
            for component in inbound_ranked[:8]
        ],
        "externals": [
            {"key": external.key, "label": external.label, "users": dict(external.users)}
            for external in analysis.externals
        ],
        "console_scripts": meta.console_scripts,
        "dependencies": meta.dependencies,
        "coverage": coverage.as_dict(),
    }
    return Explanation(overview=overview, sections=sections, facts=facts)
