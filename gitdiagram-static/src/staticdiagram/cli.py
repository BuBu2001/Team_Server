"""Command line interface.

    staticdiagram owner/repo
    staticdiagram ./path/to/project
    staticdiagram https://github.com/owner/repo --branch dev --max-nodes 20
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from .depgraph import AnalyzeOptions
from .pipeline import ALL_FORMATS, generate

__version__ = "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="staticdiagram",
        description=(
            "Turn a repository into an architecture diagram with static analysis only. "
            "No model calls, no API keys, no network beyond fetching the repository."
        ),
    )
    parser.add_argument(
        "target",
        help="owner/repo, a git URL, or a local directory",
    )
    parser.add_argument("--branch", help="branch or ref to analyse (default: repository default)")
    parser.add_argument("--out", type=Path, default=Path("out"), help="output directory (default: ./out)")
    parser.add_argument(
        "--format",
        action="append",
        choices=[*ALL_FORMATS, "all"],
        help="artefacts to write (repeatable; default: all)",
    )
    parser.add_argument("--max-nodes", type=int, default=26, help="component budget (max 34)")
    parser.add_argument("--max-edges", type=int, default=40, help="relation budget (max 48)")
    parser.add_argument("--max-groups", type=int, default=6, help="subsystem budget (max 10)")
    parser.add_argument("--depth", type=int, help="force the directory depth used for components")
    parser.add_argument("--include-tests", action="store_true", help="analyse test files too")
    parser.add_argument("--no-external", action="store_true", help="omit external system nodes")
    parser.add_argument("--no-actors", action="store_true", help="omit actor nodes")
    parser.add_argument("--max-files", type=int, default=20_000, help="cap on parsed files")
    parser.add_argument("--title", help="diagram title (default: package or repository name)")
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub token for private repositories (default: $GITHUB_TOKEN)",
    )
    parser.add_argument(
        "--png",
        action="store_true",
        help="also rasterise the SVG when rsvg-convert or resvg is installed",
    )
    parser.add_argument("--stdout", action="store_true", help="print the Mermaid source and exit")
    parser.add_argument("--keep-cache", action="store_true", help="keep the cloned repository on disk")
    parser.add_argument("--quiet", action="store_true", help="only report errors")
    parser.add_argument("--version", action="version", version=f"staticdiagram {__version__}")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    options = AnalyzeOptions(
        max_nodes=args.max_nodes,
        max_edges=args.max_edges,
        max_groups=args.max_groups,
        depth=args.depth,
        include_tests=args.include_tests,
        max_files=args.max_files,
        external_nodes=not args.no_external,
        actor_nodes=not args.no_actors,
    )

    if args.keep_cache:
        os.environ["STATICDIAGRAM_KEEP_CACHE"] = "1"

    target = args.target
    if args.branch and "/" in target and not Path(target).exists():
        target = f"{target}@{args.branch}"

    formats = args.format or ["all"]
    try:
        result = generate(
            target,
            out_dir=args.out,
            options=options,
            formats=formats,
            token=args.token,
            title=args.title,
            png=args.png,
        )
    except (FileNotFoundError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.stdout:
        print(result.mermaid)
        return 0

    coverage = result.analysis.coverage
    if not args.quiet:
        graph = result.graph
        print(f"{result.repo.slug}")
        print(
            f"  parsed {coverage.files_parsed} python modules, "
            f"resolved {coverage.imports_resolved} internal imports"
        )
        print(
            f"  graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges, "
            f"{len(graph.groups)} groups (depth {coverage.depth})"
        )
        for path in result.outputs.files:
            print(f"  wrote {path}")
    if result.issues:
        print(
            f"  warning: {len(result.issues)} validation issue(s): "
            + "; ".join(str(issue) for issue in result.issues[:3]),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
