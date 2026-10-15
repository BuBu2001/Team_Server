"""From source files to a `DiagramGraph`, deterministically.

Pipeline:
  1. parse every product Python file (`pyanalysis`)
  2. resolve each import to a repository path (`resolver`) -> file-level edges
  3. aggregate files into components (packages / modules)
  4. rank components with weighted PageRank + size + entry-point bonuses
  5. cluster components into groups, pick nodes and edges within GitDiagram's
     limits, derive shapes and labels
  6. add external actors and stores that the imports prove exist

Every edge keeps the file and line that produced it, which becomes the graph's
`evidencePath` — the same field GitDiagram asks a model to cite.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .ingest import (
    INFRA,
    ProjectMeta,
    Repository,
    is_python_source,
    is_test_path,
)
from .model import (
    MAX_EDGES,
    MAX_GROUPS,
    MAX_NODES,
    DiagramGraph,
    Edge,
    Group,
    Node,
    clamp_text,
    make_id,
)
from .pyanalysis import STDLIB, FileFacts, analyze_source
from .resolver import ModuleIndex, build_index, resolve_import

ACRONYMS = {
    "api": "API",
    "apis": "APIs",
    "cli": "CLI",
    "db": "DB",
    "ui": "UI",
    "http": "HTTP",
    "https": "HTTPS",
    "sql": "SQL",
    "orm": "ORM",
    "sdk": "SDK",
    "mcp": "MCP",
    "id": "ID",
    "ids": "IDs",
    "url": "URL",
    "urls": "URLs",
    "uri": "URI",
    "json": "JSON",
    "yaml": "YAML",
    "yml": "YML",
    "csv": "CSV",
    "aws": "AWS",
    "gcp": "GCP",
    "s3": "S3",
    "crud": "CRUD",
    "auth": "Auth",
    "oauth": "OAuth",
    "jwt": "JWT",
    "grpc": "gRPC",
    "rpc": "RPC",
    "html": "HTML",
    "css": "CSS",
    "sse": "SSE",
    "kv": "KV",
    "llm": "LLM",
    "llms": "LLMs",
    "ai": "AI",
    "ml": "ML",
    "ci": "CI",
    "cd": "CD",
    "etl": "ETL",
    "crud": "CRUD",
    "websocket": "WebSocket",
    "websockets": "WebSockets",
    "postgres": "Postgres",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "redis": "Redis",
    "mongodb": "MongoDB",
    "kafka": "Kafka",
    "rabbitmq": "RabbitMQ",
    "celery": "Celery",
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "pydantic": "Pydantic",
    "kubernetes": "Kubernetes",
    "k8s": "K8s",
    "terraform": "Terraform",
    "docker": "Docker",
    "github": "GitHub",
    "git": "Git",
    "svg": "SVG",
    "png": "PNG",
    "pdf": "PDF",
    "tts": "TTS",
    "ocr": "OCR",
}

GENERIC_LABELS = {
    "utils",
    "util",
    "helpers",
    "helper",
    "common",
    "core",
    "lib",
    "libs",
    "misc",
    "shared",
    "base",
    "internal",
    "other",
    "support",
    "tools",
    "tooling",
    "src",
    "app",
    "pkg",
    "package",
    "module",
    "modules",
}

# Third-party roots and compose images that prove an external system, and how
# to draw it. `signals` are the specific facts from pyanalysis, never a generic
# family tag: `state:sql` alone must not conjure MySQL, SQLite and Postgres.
@dataclass(frozen=True)
class ExternalRule:
    key: str
    label: str
    shape: str
    signals: tuple[str, ...]
    roots: tuple[str, ...]
    images: tuple[str, ...] = ()
    verb: str = "uses"


EXTERNAL_RULES: tuple[ExternalRule, ...] = (
    ExternalRule("postgres", "PostgreSQL", "database", ("driver:postgres",),
                 ("psycopg", "psycopg2", "asyncpg"), ("postgres", "postgis"), "queries"),
    ExternalRule("mysql", "MySQL", "database", ("driver:mysql",),
                 ("pymysql", "mysqlclient", "MySQLdb"), ("mysql", "mariadb"), "queries"),
    ExternalRule("sqlite", "SQLite", "database", ("driver:sqlite",),
                 ("sqlite3", "sqlalchemy",), ("sqlite",), "queries"),
    ExternalRule("mongo", "MongoDB", "database", ("driver:mongo",),
                 ("pymongo", "motor"), ("mongo",), "queries"),
    ExternalRule("redis", "Redis", "database", ("driver:redis",),
                 ("redis", "aioredis"), ("redis", "valkey", "keydb"), "caches in"),
    ExternalRule("s3", "Object storage (S3)", "database", ("object_storage",),
                 ("boto3", "minio", "botocore"), ("minio", "localstack"), "stores in"),
    ExternalRule("search", "Elasticsearch", "database", ("state:search",),
                 ("elasticsearch", "opensearchpy"), ("elasticsearch", "opensearch"), "queries"),
    ExternalRule("celery", "Celery broker", "queue", ("queue:celery",),
                 ("celery",), ("celery",), "enqueues"),
    ExternalRule("kafka", "Kafka", "queue", ("streaming",),
                 ("kafka", "confluent_kafka"), ("kafka", "redpanda"), "publishes"),
    ExternalRule("rabbitmq", "RabbitMQ", "queue", ("queue:rabbitmq",),
                 ("pika", "aio_pika"), ("rabbitmq",), "publishes"),
    ExternalRule("http", "External HTTP APIs", "hexagon", ("http_client",),
                 ("requests", "httpx", "aiohttp", "urllib3"), (), "calls"),
    ExternalRule("llm", "LLM provider", "hexagon", ("ai:openai", "ai:anthropic", "ai:orchestration"),
                 ("openai", "anthropic", "langchain", "litellm", "google.genai"), (), "calls"),
    ExternalRule("stripe", "Stripe", "hexagon", ("external:payments",), ("stripe",), (), "charges via"),
    ExternalRule("smtp", "Mail provider", "hexagon", ("external:email",),
                 ("smtplib", "sendgrid", "aiosmtplib"), ("mailhog",), "sends mail via"),
    ExternalRule("kubernetes", "Kubernetes API", "hexagon", ("infra:kubernetes",),
                 ("kubernetes",), (), "controls"),
)

# Used only when SQL is clearly present but no specific driver is.
GENERIC_SQL = ExternalRule(
    "sql", "SQL database", "database", ("state:sql",), ("sqlalchemy", "sqlmodel", "databases"),
    (), "queries",
)

MAX_EXTERNAL_NODES = 5


@dataclass
class AnalyzeOptions:
    max_nodes: int = 26
    max_edges: int = 40
    max_groups: int = 6
    depth: Optional[int] = None
    include_tests: bool = False
    max_files: int = 20_000
    max_file_bytes: int = 512_000
    external_nodes: bool = True
    actor_nodes: bool = True


@dataclass
class FileEdge:
    src: str
    dst: str
    lineno: int
    module: str
    kind: str = "import"


@dataclass
class Component:
    path: str
    kind: str = "package"  # package | module | file | external | actor
    files: list[str] = field(default_factory=list)
    loc: int = 0
    signals: set[str] = field(default_factory=set)
    third_party: Counter = field(default_factory=Counter)
    docstring: Optional[str] = None
    entrypoints: list[str] = field(default_factory=list)
    routes: int = 0
    tasks: int = 0
    commands: int = 0
    classes: int = 0
    inbound_from: int = 0
    outbound_to: int = 0
    env_vars: set[str] = field(default_factory=set)
    inbound: int = 0
    outbound: int = 0
    pagerank: float = 0.0
    score: float = 0.0
    node_id: str = ""
    label: str = ""
    group_path: Optional[str] = None
    primary_file: Optional[str] = None

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass
class ComponentEdge:
    src: str
    dst: str
    weight: int = 0
    evidence_path: Optional[str] = None
    evidence_lineno: Optional[int] = None
    modules: Counter = field(default_factory=Counter)


@dataclass
class ExternalNode:
    key: str
    label: str
    shape: str
    node_id: str
    users: Counter = field(default_factory=Counter)
    evidence: dict[str, tuple[str, int]] = field(default_factory=dict)
    kind: str = "system"  # system | actor
    verb: str = "uses"


@dataclass
class Coverage:
    files_total: int = 0
    files_parsed: int = 0
    files_failed: int = 0
    files_skipped_tests: int = 0
    files_skipped_other: int = 0
    import_edges: int = 0
    imports_resolved: int = 0
    imports_unresolved: int = 0
    imports_stdlib: int = 0
    unresolved_names: Counter = field(default_factory=Counter)
    syntax_errors: list[tuple[str, str]] = field(default_factory=list)
    components_total: int = 0
    components_shown: int = 0
    depth: int = 1

    def as_dict(self) -> dict:
        return {
            "files_total": self.files_total,
            "files_parsed": self.files_parsed,
            "files_failed": self.files_failed,
            "files_skipped_tests": self.files_skipped_tests,
            "files_skipped_other": self.files_skipped_other,
            "import_edges": self.import_edges,
            "imports_resolved": self.imports_resolved,
            "imports_unresolved": self.imports_unresolved,
            "imports_stdlib": self.imports_stdlib,
            "components_total": self.components_total,
            "components_shown": self.components_shown,
            "component_depth": self.depth,
            "top_unresolved": self.unresolved_names.most_common(15),
            "syntax_errors": self.syntax_errors[:10],
        }


@dataclass
class Analysis:
    repo: Repository
    meta: ProjectMeta
    options: AnalyzeOptions
    facts: dict[str, FileFacts] = field(default_factory=dict)
    index: Optional[ModuleIndex] = None
    file_edges: list[FileEdge] = field(default_factory=list)
    components: dict[str, Component] = field(default_factory=dict)
    component_edges: dict[tuple[str, str], ComponentEdge] = field(default_factory=dict)
    externals: list[ExternalNode] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    graph: Optional[DiagramGraph] = None
    infra: dict = field(default_factory=dict)

    def component_list(self) -> list[Component]:
        return sorted(self.components.values(), key=lambda item: -item.score)

    def selected(self) -> list[Component]:
        if self.graph is None:
            return []
        chosen = self.graph.node_ids()
        return [c for c in self.component_list() if c.node_id in chosen]


# --- labels -----------------------------------------------------------------


def _plural(count: int, word: str = "") -> str:
    """Plural suffix; `word` is accepted for readability at call sites."""
    return "" if count == 1 else "s"


def docstring_summary(docstring: str, limit: int = 34) -> Optional[str]:
    """First sentence, cut at a word boundary — used as a node's second line."""
    text = re.sub(r"\s+", " ", docstring.strip())
    text = re.split(r"(?<=\.)\s|\s\|\s|\s-\s", text)[0].rstrip(".")
    text = re.sub(
        r"^(?:this (?:module|package|file)|module|package|namespace)\s+", "", text, flags=re.I
    )
    if not text:
        return None
    if len(text) > limit:
        cut = text[: limit - 1].rsplit(" ", 1)[0]
        text = f"{cut}\u2026" if cut else text[:limit]
    return text


def humanize(segment: str) -> str:
    """`user_sessions` / `UserSessions` / `api-v2` -> readable title."""
    if not segment:
        return segment
    if "_" in segment or "-" in segment or "." in segment:
        words = re.split(r"[_\-.]+", segment)
    else:
        words = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", segment)
        if len(words) > 1:
            return " ".join(
                ACRONYMS.get(word.lower(), word if word.isupper() else word.capitalize())
                for word in words
            )
    out: list[str] = []
    for word in words:
        if not word:
            continue
        out.append(ACRONYMS.get(word.lower(), word.capitalize()))
    return " ".join(out)


def component_label(component: Component) -> str:
    name = component.name
    if name.endswith(".py"):
        stem = name[:-3]
        known = ENTRY_LABELS.get(stem.lower())
        if known:
            return clamp_text(known, 44)
        name = stem
    label = humanize(name)
    if component.files and all(BARREL_FILE.search(path) for path in component.files):
        return clamp_text(f"{label} package root", 44)
    if name.lower() in GENERIC_LABELS and component.docstring:
        derived = label_from_docstring(component.docstring)
        if derived:
            label = derived
    return clamp_text(label, 44) or "Module"


def label_from_docstring(docstring: str, max_words: int = 4) -> Optional[str]:
    text = docstring.strip().rstrip(".")
    text = re.sub(r"^\s*(?:this\s+(?:module|package|file)|module|package)\s+", "", text, flags=re.I)
    words = text.split()
    if not words or len(words) > max_words + 3:
        return None
    phrase = " ".join(words[:max_words])
    if not re.match(r"^[A-Za-z0-9 ,/'\-()]+$", phrase):
        return None
    return humanize_title(phrase)


def humanize_title(phrase: str) -> str:
    words = phrase.split()
    out = []
    for index, word in enumerate(words):
        lowered = word.lower()
        if lowered in ACRONYMS:
            out.append(ACRONYMS[lowered])
        elif index == 0 or len(word) > 2:
            out.append(word.capitalize() if lowered == word else word)
        else:
            out.append(word)
    return " ".join(out)


# --- components -------------------------------------------------------------


SOURCE_BASES = ("src", "lib", "app", "python", "packages", "source")

# A file that starts a process is worth its own box: readers look for it.
ENTRY_FILE = re.compile(
    r"(?:^|/)(?:main|app|application|server|cli|__main__|manage|wsgi|asgi|run|start)\.py$"
)
BARREL_FILE = re.compile(r"(?:^|/)__init__\.py$")
ENTRY_LABELS = {
    "main": "Application entry",
    "app": "Application",
    "application": "Application",
    "server": "Server",
    "cli": "CLI",
    "__main__": "CLI entry",
    "manage": "Management commands",
    "wsgi": "WSGI entry",
    "asgi": "ASGI entry",
    "run": "Runner",
    "start": "Startup",
    "settings": "Settings",
    "config": "Configuration",
    "urls": "URL routing",
    "setup": "Packaging",
    "conftest": "Test fixtures",
}


def _detect_base(paths: list[str]) -> str:
    """`src` when (almost) every product file lives under it."""
    if not paths:
        return ""
    tops = Counter(path.split("/")[0] for path in paths if "/" in path)
    if len(tops) == 1:
        candidate = next(iter(tops))
        if candidate in SOURCE_BASES:
            return candidate
    return ""


def _per_file_buckets(paths: list[str]) -> dict[str, list[str]]:
    """One bucket per module; `__init__.py` stays with its package."""
    buckets: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        if BARREL_FILE.search(path):
            key = path.rsplit("/", 1)[0] if "/" in path else path
        else:
            key = path
        buckets[key].append(path)
    return dict(buckets)


def assign_components(
    paths: list[str], facts: dict[str, FileFacts], depth: Optional[int]
) -> tuple[dict[str, Component], int]:
    """Bucket files into components at an adaptive directory depth.

    Depth is measured below the source base (`src/`), so a conventional
    `src/pkg/api/routes.py` layout yields `src/pkg/api` at level 1. The chosen
    level is the shallowest one that splits the code into several balanced
    buckets; deeper levels only appear when the top of the tree is one big pile.
    """
    if not paths:
        return {}, 1
    base = _detect_base(paths)
    prefix = f"{base}/" if base else ""

    def key_for(path: str, level: int) -> str:
        relative = path[len(prefix) :] if prefix and path.startswith(prefix) else path
        parts = relative.split("/")
        if len(parts) == 1:
            return path  # a top-level file is its own component
        cut = min(level, len(parts) - 1)
        return f"{prefix}{'/'.join(parts[:cut])}"

    def partition(level: int) -> dict[str, list[str]]:
        buckets: dict[str, list[str]] = defaultdict(list)
        for path in paths:
            buckets[key_for(path, level)].append(path)
        return dict(buckets)

    if depth is not None:
        effective_depth = max(1, depth)
    else:
        target_min = 5
        target_max = MAX_NODES + 6
        effective_depth = 1
        fallback: Optional[int] = None
        for level in range(1, 6):
            buckets = partition(level)
            count = len(buckets)
            if count < 2 or count > target_max:
                continue
            if fallback is None:
                fallback = level
            dominant = max(len(members) for members in buckets.values()) / len(paths)
            if count >= target_min and dominant <= 0.6:
                effective_depth = level
                break
        else:
            effective_depth = fallback or 1

    buckets = partition(effective_depth)

    # A flat single-package repository (requests, flask) has no directory
    # structure to group by: fall back to one component per module.
    if len(buckets) < min(4, max(2, len(paths) // 4)):
        per_file = _per_file_buckets(paths)
        if len(per_file) > len(buckets):
            buckets = per_file
            effective_depth = 0  # 0 reports "module level" in the coverage line

    # A bucket holding most of the code hides the interesting modules; split it
    # the same way (fastapi's root package next to its subpackages).
    threshold = max(10, int(0.3 * len(paths)))
    for key, members in list(buckets.items()):
        if len(members) > threshold:
            split = _per_file_buckets(members)
            if len(split) > 1:
                del buckets[key]
                buckets.update(split)

    # Pull process entry points out of the directory bucket they sit in, so
    # "the HTTP app" and "the CLI" are boxes of their own.
    split: dict[str, list[str]] = {}
    for key, members in list(buckets.items()):
        if len(members) < 3:
            continue
        entries = sorted(path for path in members if ENTRY_FILE.search(path))[:2]
        if not entries:
            continue
        remaining = [path for path in members if path not in entries]
        if not remaining:
            continue
        buckets[key] = remaining
        for entry in entries:
            split[entry] = [entry]
    buckets.update(split)

    components: dict[str, Component] = {}
    for key, members in buckets.items():
        component = Component(path=key, kind="package" if len(members) > 1 else "module")
        component.files = sorted(members)
        components[key] = component

    for component in components.values():
        _fill_component(component, facts)
    return components, effective_depth


def _fill_component(component: Component, facts: dict[str, FileFacts]) -> None:
    signals: set[str] = set()
    third_party: Counter = Counter()
    env_vars: set[str] = set()
    docstring: Optional[str] = None
    entrypoints: list[str] = []
    routes = tasks = commands = classes = loc = 0
    best_primary: Optional[tuple[int, str]] = None

    for path in component.files:
        item = facts.get(path)
        if item is None:
            continue
        loc += item.loc
        signals |= item.signals
        third_party.update(item.third_party)
        env_vars |= item.env_vars
        routes += len(item.routes)
        tasks += len(item.tasks)
        commands += len(item.commands)
        classes += len(item.classes)
        if item.entrypoint:
            entrypoints.append(path)
        if item.is_package_init and item.docstring and not docstring:
            docstring = item.docstring
        rank = (item.loc, -len(path))
        if not path.endswith("__init__.py"):
            rank = (item.loc + 1000, -len(path))
        if best_primary is None or rank > best_primary[0]:
            best_primary = (rank, path)

    if docstring is None:
        for path in component.files:
            item = facts.get(path)
            if item and item.docstring:
                docstring = item.docstring
                break

    component.loc = loc
    component.signals = signals
    component.third_party = third_party
    component.env_vars = env_vars
    component.docstring = docstring
    component.entrypoints = entrypoints
    component.routes = routes
    component.tasks = tasks
    component.commands = commands
    component.classes = classes
    component.primary_file = best_primary[1] if best_primary else (
        component.files[0] if component.files else None
    )
    component.kind = "module" if len(component.files) == 1 else "package"
    if component.path.endswith(".py"):
        component.kind = "file"


# --- graph building ---------------------------------------------------------


def build_file_edges(
    facts: dict[str, FileFacts], index: ModuleIndex, coverage: Coverage
) -> list[FileEdge]:
    edges: list[FileEdge] = []
    seen: set[tuple[str, str, int]] = set()
    for path, item in facts.items():
        for entry in item.imports:
            if entry.level == 0 and entry.top in STDLIB:
                coverage.imports_stdlib += 1
                continue
            target = resolve_import(index, entry, path)
            if target is None:
                coverage.imports_unresolved += 1
                if entry.level == 0 and entry.top:
                    coverage.unresolved_names[entry.top] += 1
                continue
            if target == path:
                continue
            coverage.imports_resolved += 1
            key = (path, target, entry.lineno)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                FileEdge(
                    src=path,
                    dst=target,
                    lineno=entry.lineno,
                    module=entry.module,
                    kind=entry.kind,
                )
            )
    coverage.import_edges = len(edges)
    return edges


def aggregate_components(
    file_edges: Iterable[FileEdge], components: dict[str, Component]
) -> dict[tuple[str, str], ComponentEdge]:
    owner: dict[str, str] = {}
    for key, component in components.items():
        for path in component.files:
            owner[path] = key

    aggregated: dict[tuple[str, str], ComponentEdge] = {}
    per_source_out: Counter = Counter()
    for edge in file_edges:
        src = owner.get(edge.src)
        dst = owner.get(edge.dst)
        if not src or not dst or src == dst:
            continue
        per_source_out[edge.src] += 1
        key = (src, dst)
        entry = aggregated.get(key)
        if entry is None:
            entry = ComponentEdge(src=src, dst=dst, evidence_path=edge.src, evidence_lineno=edge.lineno)
            aggregated[key] = entry
        entry.weight += 1
        entry.modules[edge.module] += 1

    # Evidence: the importing file that carries the most weight for this pair,
    # preferring a package `__init__.py` and then the shortest path.
    best_evidence: dict[tuple[str, str], tuple[int, int, str, int]] = {}
    for edge in file_edges:
        src = owner.get(edge.src)
        dst = owner.get(edge.dst)
        if not src or not dst or src == dst:
            continue
        key = (src, dst)
        rank = (
            per_source_out[edge.src],
            1 if edge.src.endswith("__init__.py") else 0,
            -len(edge.src),
        )
        current = best_evidence.get(key)
        if current is None or rank > (current[0], current[1], current[2], 0)[:3]:
            best_evidence[key] = (rank[0], rank[1], rank[2], edge.lineno)
            if key in aggregated:
                aggregated[key].evidence_path = edge.src
                aggregated[key].evidence_lineno = edge.lineno
    return aggregated


def pagerank(
    components: dict[str, Component],
    edges: dict[tuple[str, str], ComponentEdge],
    damping: float = 0.85,
    iterations: int = 40,
) -> dict[str, float]:
    nodes = list(components)
    if not nodes:
        return {}
    out_weight: Counter = Counter()
    inbound: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (src, dst), edge in edges.items():
        weight = float(max(edge.weight, 1))
        out_weight[src] += weight
        inbound[dst].append((src, weight))

    rank = {node: 1.0 / len(nodes) for node in nodes}
    for _ in range(iterations):
        dangling = sum(value for node, value in rank.items() if out_weight[node] == 0)
        next_rank = {node: (1.0 - damping) / len(nodes) + damping * dangling / len(nodes) for node in nodes}
        for node, incoming in inbound.items():
            total = 0.0
            for src, weight in incoming:
                if out_weight[src] <= 0:
                    continue
                total += rank[src] * weight / out_weight[src]
            next_rank[node] += damping * total
        norm = sum(next_rank.values()) or 1.0
        rank = {node: value / norm for node, value in next_rank.items()}
    return rank


def score_components(
    components: dict[str, Component],
    edges: dict[tuple[str, str], ComponentEdge],
    ranks: dict[str, float],
) -> None:
    inbound: Counter = Counter()
    outbound: Counter = Counter()
    inbound_from: dict[str, set[str]] = defaultdict(set)
    outbound_to: dict[str, set[str]] = defaultdict(set)
    for (src, dst), edge in edges.items():
        outbound[src] += edge.weight
        inbound[dst] += edge.weight
        outbound_to[src].add(dst)
        inbound_from[dst].add(src)

    max_loc = max((component.loc for component in components.values()), default=1) or 1
    max_inbound = max(inbound.values(), default=1) or 1
    for path, component in components.items():
        component.inbound = inbound[path]
        component.outbound = outbound[path]
        component.inbound_from = len(inbound_from[path])
        component.outbound_to = len(outbound_to[path])
        component.pagerank = ranks.get(path, 0.0)
        depth_bonus = 0.06 * max(0, 3 - component.path.count("/"))
        component.score = (
            component.pagerank * 3.2
            + 0.35 * (component.loc / max_loc)
            + 0.30 * (component.inbound / max_inbound)
            + (0.30 if component.entrypoints else 0.0)
            + 0.012 * component.routes
            + 0.012 * component.tasks
            + depth_bonus
        )


# --- externals --------------------------------------------------------------


DATA_OWNER_HINT = re.compile(
    r"(?:^|/)(?:db|database|models?|storage|persistence|repositories|state|cache|"
    r"workers?|tasks?|integrations?|clients?|infra)(?:/|\.|$)",
    re.IGNORECASE,
)


def _infra_owner(components: dict[str, Component], rule: ExternalRule) -> Optional[str]:
    """Best component to hang an infra-only external system off."""
    if not components:
        return None
    ranked = sorted(components.values(), key=lambda item: -item.score)
    wants_data = rule.shape in {"database", "queue"}
    for component in ranked:
        if DATA_OWNER_HINT.search(component.path) and (component.files or True):
            if wants_data or True:
                return component.path
    return ranked[0].path


def detect_externals(
    facts: dict[str, FileFacts],
    components: dict[str, Component],
    meta: ProjectMeta,
    infra: dict,
    options: AnalyzeOptions,
) -> list[ExternalNode]:
    """External systems the code or the infrastructure proves exist."""
    if not options.external_nodes:
        return []

    signals: Counter = Counter()
    third_party: Counter = Counter()
    owners: dict[str, str] = {}
    for key, component in components.items():
        for path in component.files:
            owners[path] = key

    for item in facts.values():
        third_party.update(item.third_party)
        for signal in item.signals:
            signals[signal] += 1

    def proof_for(rule: ExternalRule) -> Optional[tuple[str, int]]:
        """The file and line where this system becomes visible."""
        best: Optional[tuple[str, int]] = None
        for path, item in facts.items():
            hit_line = 0
            if any(signal in item.signals for signal in rule.signals):
                hit_line = item.imports[0].lineno if item.imports else 1
            elif any(root in item.third_party for root in rule.roots):
                hit_line = item.imports[0].lineno if item.imports else 1
            if hit_line and (best is None or path < best[0]):
                best = (path, hit_line)
        return best

    def users_of(rule: ExternalRule) -> Counter:
        users: Counter = Counter()
        for path, item in facts.items():
            if any(signal in item.signals for signal in rule.signals) or any(
                root in item.third_party for root in rule.roots
            ):
                component = owners.get(path)
                if component:
                    users[component] += 1
        return users

    found: list[ExternalNode] = []
    taken_ids: set[str] = set()
    specific_sql = False
    for rule in EXTERNAL_RULES:
        direct = sum(signals.get(signal, 0) for signal in rule.signals)
        via_roots = sum(count for root, count in third_party.items() if root in rule.roots)
        image_hit = next(
            (
                service
                for service in infra.get("services", [])
                if any(needle in (service.get("image") or "").lower() for needle in rule.images)
            ),
            None,
        )
        if not (direct or via_roots or image_hit):
            continue
        # `sqlalchemy` alone must not claim SQLite; only a real import can.
        if rule.key == "sqlite" and not third_party.get("sqlite3") and "driver:sqlite" not in signals:
            continue
        if rule.shape == "database" and rule.key in {"postgres", "mysql", "mongo", "sqlite"}:
            specific_sql = True
        users = users_of(rule)
        evidence = proof_for(rule)
        if image_hit is not None:
            if evidence is None:
                evidence = (image_hit.get("evidence", "docker-compose.yml"), 1)
            if not users:
                # Only the infrastructure shows this system: attach it to the
                # component that plausibly owns the connection (db/session/
                # storage/models), else to the most central one.
                owner = _infra_owner(components, rule)
                if owner:
                    users = Counter({owner: 1})
        node = ExternalNode(
            key=rule.key,
            label=rule.label,
            shape=rule.shape,
            node_id=make_id(f"ext_{rule.key}", taken_ids),
            users=users,
            kind="system",
        )
        node.verb = rule.verb  # type: ignore[attr-defined]
        if evidence:
            node.evidence[evidence[0]] = evidence
        found.append(node)

    if not specific_sql and (signals.get("state:sql") or third_party.get("sqlalchemy")):
        users = users_of(GENERIC_SQL)
        evidence = proof_for(GENERIC_SQL)
        node = ExternalNode(
            key=GENERIC_SQL.key,
            label=GENERIC_SQL.label,
            shape=GENERIC_SQL.shape,
            node_id=make_id("ext_sql", taken_ids),
            users=users,
            kind="system",
        )
        node.verb = GENERIC_SQL.verb  # type: ignore[attr-defined]
        if evidence:
            node.evidence[evidence[0]] = evidence
        found.append(node)

    found.sort(key=lambda node: (-sum(node.users.values()), node.key))
    return found[:MAX_EXTERNAL_NODES]


def detect_actors(
    facts: dict[str, FileFacts],
    components: dict[str, Component],
    meta: ProjectMeta,
    options: AnalyzeOptions,
) -> list[ExternalNode]:
    if not options.actor_nodes:
        return []
    signals: set[str] = set()
    for item in facts.values():
        signals |= item.signals

    actors: list[ExternalNode] = []
    taken: set[str] = set()
    if "http_server" in signals:
        framework = next(
            (
                ACRONYMS.get(value.split(":")[1], value.split(":")[1].capitalize())
                for value in sorted(signals)
                if value.startswith("web:") and value != "web:aiohttp"
            ),
            "HTTP",
        )
        actors.append(
            ExternalNode(
                key="http_client",
                label=f"{framework} client",
                shape="circle",
                node_id=make_id("actor_http_client", taken),
                kind="actor",
            )
        )
    if meta.console_scripts or signals & {"cli:click", "cli:typer", "cli:argparse", "cli:fire"}:
        actors.append(
            ExternalNode(
                key="cli_user",
                label="Terminal user",
                shape="circle",
                node_id=make_id("actor_cli_user", taken),
                kind="actor",
            )
        )
    if signals & {"background"}:
        actors.append(
            ExternalNode(
                key="scheduler",
                label="Scheduler / broker",
                shape="circle",
                node_id=make_id("actor_scheduler", taken),
                kind="actor",
            )
        )
    # A library with only debug `__main__` guards has no actor worth drawing.
    return actors[:3]


# --- shape and type ---------------------------------------------------------


def shape_for(component: Component) -> Optional[str]:
    signals = component.signals
    name = component.path.lower()
    if signals & {"state:sql", "state:document_db", "state:cache", "orm:model"} or re.search(
        r"(?:^|/)(?:models?|db|database|storage|repositories|persistence|migrations?)(?:/|$)", name
    ):
        return "database"
    if signals & {"background", "streaming"} or re.search(
        r"(?:^|/)(?:workers?|tasks?|queues?|jobs?|consumers?|celery)(?:/|$)", name
    ):
        return "queue"
    if re.search(r"(?:^|/)(?:schemas?|contracts?|types?|proto)\b", name) or (
        "schema:pydantic" in signals and component.classes and not component.routes
    ):
        return "document"
    if re.search(r"(?:^|/)(?:integrations?|clients?|external|providers?|plugins?)(?:/|$)", name):
        return "hexagon"
    return None


def type_for(component: Component) -> str:
    if component.routes and component.tasks:
        return "routes and tasks"
    if component.routes:
        return f"{component.routes} route{'s' if component.routes != 1 else ''}"
    if component.tasks:
        return f"{component.tasks} task{'s' if component.tasks != 1 else ''}"
    if component.commands:
        return f"{component.commands} CLI command{_plural(component.commands)}"
    if "orm:model" in component.signals:
        return "data models"
    if component.docstring:
        summary = docstring_summary(component.docstring, 34)
        lowered = summary.lower() if summary else ""
        # GitDiagram's compiler hides a `type` that merely repeats the label,
        # so prefer the structural fact in that case.
        looks_like_path = bool(re.fullmatch(r"[\w.\-/ ]+", summary or "")) and (
            "." in (summary or "") or "/" in (summary or "")
        )
        if (
            summary
            and not looks_like_path
            and component.label.lower() not in lowered
            and lowered not in component.label.lower()
        ):
            return clamp_text(summary, 40)
    if component.kind == "package":
        return f"{len(component.files)} modules"
    # Generic on purpose: the compiler then prints the file name as the hint.
    return "module"


# --- assembly ---------------------------------------------------------------


def _path_of_node(node: Node, chosen: list[Component]) -> str:
    for component in chosen:
        if component.node_id == node.id:
            return component.path
    return node.path or ""


def build_graph(
    components: dict[str, Component],
    component_edges: dict[tuple[str, str], ComponentEdge],
    externals: list[ExternalNode],
    actors: list[ExternalNode],
    options: AnalyzeOptions,
    repo: Repository,
) -> DiagramGraph:
    ordered = sorted(components.values(), key=lambda item: -item.score)
    if not ordered:
        return DiagramGraph()

    max_nodes = max(3, min(options.max_nodes, MAX_NODES))
    chosen: list[Component] = []
    chosen_paths: set[str] = set()

    # Entry points and the biggest package always make the cut.
    for component in ordered:
        if component.entrypoints and len(chosen) < max_nodes:
            chosen.append(component)
            chosen_paths.add(component.path)
    for component in ordered:
        if len(chosen) >= max_nodes:
            break
        if component.path in chosen_paths:
            continue
        chosen.append(component)
        chosen_paths.add(component.path)

    # Keep only edges between chosen components.
    kept_edges = [
        edge
        for (src, dst), edge in component_edges.items()
        if src in chosen_paths and dst in chosen_paths
    ]
    kept_edges.sort(key=lambda edge: (-edge.weight, edge.src, edge.dst))

    connected = {edge.src for edge in kept_edges} | {edge.dst for edge in kept_edges}
    # Drop leaf noise, but never an entry point or a public surface.
    pruned = [
        component
        for component in chosen
        if component.path in connected
        or component.entrypoints
        or component.routes
        or component.tasks
    ]
    if len(pruned) < 3:
        pruned = chosen[:max_nodes]
    chosen = pruned[:max_nodes]
    chosen_paths = {component.path for component in chosen}
    kept_edges = [
        edge for edge in kept_edges if edge.src in chosen_paths and edge.dst in chosen_paths
    ]
    chosen_paths = {component.path for component in chosen}
    kept_edges = [
        edge for edge in kept_edges if edge.src in chosen_paths and edge.dst in chosen_paths
    ]

    taken_ids: set[str] = set()
    for component in sorted(chosen, key=lambda item: item.path):
        component.node_id = make_id(component.path.replace("/", "_").replace(".", "_"), taken_ids)
        component.label = component_label(component)

    # Two packages may both be `app.py`: qualify the later ones so a reader
    # never sees identical boxes meaning different code.
    seen_labels: dict[str, int] = {}
    for component in sorted(chosen, key=lambda item: item.path):
        count = seen_labels.get(component.label, 0)
        seen_labels[component.label] = count + 1
        if count:
            parts = component.path.split("/")
            qualifier = parts[-2] if len(parts) >= 2 else ""
            suffix = humanize(qualifier) if qualifier and qualifier not in SOURCE_BASES else str(count + 1)
            component.label = clamp_text(f"{component.label} ({suffix})", 44)

    # Groups: the shallowest ancestor directory that still splits things up.
    groups = _assign_groups(chosen, options)

    nodes: list[Node] = []
    for component in chosen:
        node_path = component.path
        if node_path not in repo.path_types:
            node_path = component.primary_file or None
        nodes.append(
            Node(
                id=component.node_id,
                label=component.label,
                type=clamp_text(type_for(component), 60),
                groupId=component.group_path,
                path=node_path,
                shape=shape_for(component),
                description=(
                    clamp_text(component.docstring, 200) if component.docstring else None
                ),
            )
        )

    edges: list[Edge] = []
    max_edges = max(4, min(options.max_edges, MAX_EDGES))
    seen_pairs: set[tuple[str, str]] = set()

    def add(edge: ComponentEdge) -> bool:
        src = components[edge.src]
        dst = components[edge.dst]
        pair = (src.node_id, dst.node_id)
        if pair in seen_pairs:
            return False
        seen_pairs.add(pair)
        edges.append(
            Edge(
                src=src.node_id,
                dst=dst.node_id,
                label=_edge_label(src, dst, edge.weight),
                style="solid",
                evidencePath=edge.evidence_path,
            )
        )
        return True

    # Coverage before weight: every component keeps its strongest relation, so
    # the edge budget can never strand a node the reader then sees floating.
    strongest: dict[str, ComponentEdge] = {}
    for edge in kept_edges:
        for endpoint in (edge.src, edge.dst):
            current = strongest.get(endpoint)
            if current is None or edge.weight > current.weight:
                strongest[endpoint] = edge
    for component in chosen:
        if len(edges) >= max_edges:
            break
        edge = strongest.get(component.path)
        if edge is not None:
            add(edge)
    for edge in kept_edges:
        if len(edges) >= max_edges:
            break
        add(edge)

    # The budget may still have orphaned a node: drop it rather than float it.
    connected = {edge.src for edge in edges} | {edge.dst for edge in edges}
    keep_ids = {
        node.id
        for node in nodes
        if node.id in connected
        or components.get(_path_of_node(node, chosen)) is None
        or components[_path_of_node(node, chosen)].entrypoints
        or components[_path_of_node(node, chosen)].routes
    }
    dropped = [node.id for node in nodes if node.id not in keep_ids]
    if dropped:
        nodes = [node for node in nodes if node.id in keep_ids]
        edges = [edge for edge in edges if edge.src in keep_ids and edge.dst in keep_ids]
        chosen = [c for c in chosen if c.node_id in keep_ids]
        for group in list(groups):
            if not any(node.groupId == group.id for node in nodes):
                groups.remove(group)

    external_edges = _external_edges(chosen, externals, actors, components, repo, edges)
    edges.extend(external_edges)

    graph_nodes = list(nodes)
    for external in externals + actors:
        graph_nodes.append(
            Node(
                id=external.node_id,
                label=clamp_text(external.label, 44),
                type="external system" if external.kind == "system" else "actor",
                groupId=None,
                path=None,
                shape=external.shape,  # type: ignore[arg-type]
                description=None,
            )
        )

    graph = DiagramGraph(
        groups=groups,
        nodes=graph_nodes[: MAX_NODES + len(externals) + len(actors)][:MAX_NODES],
        edges=edges[:MAX_EDGES],
    )
    return graph


def _edge_label(src: Component, dst: Component, weight: int) -> str:
    verb = "imports"
    if dst.signals & {"state:sql", "state:document_db", "state:cache", "orm:model"}:
        verb = "reads/writes"
    elif dst.signals & {"background", "streaming"}:
        verb = "enqueues"
    elif src.entrypoints and dst.routes:
        verb = "serves"
    elif dst.routes:
        verb = "routes to"
    elif src.tasks and dst.signals & {"http_client"}:
        verb = "calls"
    if weight > 3:
        return f"{verb} x{weight}"
    return verb


def _external_edges(
    chosen: list[Component],
    externals: list[ExternalNode],
    actors: list[ExternalNode],
    components: dict[str, Component],
    repo: Repository,
    existing: list[Edge],
) -> list[Edge]:
    by_path = {component.path: component for component in chosen}
    edges: list[Edge] = []
    seen = {(edge.src, edge.dst) for edge in existing}

    for external in externals:
        users = [
            (path, count) for path, count in external.users.most_common() if path in by_path
        ]
        if not users:
            continue
        for path, _count in users[:3]:
            component = by_path[path]
            key = (component.node_id, external.node_id)
            if key in seen:
                continue
            seen.add(key)
            evidence = next(iter(external.evidence), None)
            if evidence and evidence not in repo.path_types:
                evidence = None
            edges.append(
                Edge(
                    src=component.node_id,
                    dst=external.node_id,
                    label=_external_verb(external),
                    style="solid",
                    evidencePath=evidence,
                )
            )

    for actor in actors:
        targets: list[tuple[Component, str]] = []
        if actor.key == "http_client":
            targets = [
                (component, "requests")
                for component in sorted(chosen, key=lambda item: -item.routes)
                if component.routes
            ][:2]
        elif actor.key == "cli_user":
            targets = [
                (by_path[path], "invokes")
                for path in _cli_entry_components(chosen, components)
                if path in by_path
            ][:2]
        elif actor.key == "scheduler":
            targets = [
                (component, "triggers")
                for component in sorted(chosen, key=lambda item: -item.tasks)
                if component.tasks
            ][:2]
        else:
            targets = [
                (component, "runs")
                for component in sorted(chosen, key=entry_rank)
                if component.entrypoints
            ][:1]
        for component, verb in targets:
            key = (actor.node_id, component.node_id)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                Edge(
                    src=actor.node_id,
                    dst=component.node_id,
                    label=verb,
                    style="solid",
                    evidencePath=component.entrypoints[0] if component.entrypoints else component.primary_file,
                )
            )
    return edges


def _external_verb(external: ExternalNode) -> str:
    return external.verb or "uses"


def entry_rank(component: Component) -> tuple[int, int, float]:
    """Console-script/app entries first, debug `__main__` guards last."""
    best = 3
    for path in component.entrypoints:
        stem = path.rsplit("/", 1)[-1][:-3].lower()
        if stem in {"main", "app", "application", "server", "cli", "__main__", "manage", "wsgi", "asgi"}:
            best = min(best, 0)
        elif path.endswith(("main.py", "cli.py", "app.py", "server.py")):
            best = min(best, 0)
        else:
            best = min(best, 2)
    if component.commands:
        best = min(best, 1)
    return (best, -len(component.entrypoints), -component.score)


def _cli_entry_components(
    chosen: list[Component], components: dict[str, Component]
) -> list[str]:
    return [
        component.path
        for component in sorted(chosen, key=lambda item: -item.score)
        if component.entrypoints or "cli:click" in component.signals or "cli:typer" in component.signals
    ]


def _common_dir_prefix(paths: list[str]) -> str:
    """Deepest directory shared by every component path."""
    if not paths:
        return ""
    parts_list = [path.split("/")[:-1] for path in paths]
    prefix: list[str] = []
    for column in zip(*parts_list):
        if len(set(column)) != 1:
            break
        prefix.append(column[0])
    return "/".join(prefix)


def _assign_groups(chosen: list[Component], options: AnalyzeOptions) -> list[Group]:
    """Cluster components into subsystems.

    Grouping is only drawn when it says something: a single-package repository
    whose components already *are* the subsystems gets no group boxes at all,
    which is also what GitDiagram's prompt asks a model to do for small projects.
    """
    max_groups = max(1, min(options.max_groups, MAX_GROUPS))
    if len(chosen) <= 3:
        return []

    base = _common_dir_prefix([component.path for component in chosen])
    prefix = f"{base}/" if base else ""

    def key_of(component: Component) -> Optional[str]:
        relative = (
            component.path[len(prefix) :]
            if prefix and component.path.startswith(prefix)
            else component.path
        )
        parts = relative.split("/")
        if len(parts) < 2:
            return None  # sits directly under the shared base
        return parts[0]

    buckets: dict[str, list[Component]] = defaultdict(list)
    core: list[Component] = []
    for component in chosen:
        key = key_of(component)
        if key is None:
            core.append(component)
        else:
            buckets[key].append(component)

    final: dict[str, list[Component]] = {}
    for key, items in buckets.items():
        if len(items) >= 2:
            final[key] = items
        else:
            core.extend(items)
    if len(core) >= 2:
        final["(core)"] = core
    else:
        for component in core:
            component.group_path = None

    sizes = sorted((len(items) for items in final.values()), reverse=True)
    largest_share = sizes[0] / max(1, len(chosen)) if sizes else 1.0
    if largest_share > 0.8 or sizes[-1] < 3:
        for component in chosen:
            component.group_path = None
        return []

    if len(final) <= 1:
        for component in chosen:
            component.group_path = None
        return []

    ranked = sorted(
        final.items(), key=lambda item: (-sum(c.score for c in item[1]), item[0])
    )
    if len(ranked) > max_groups:
        head = ranked[: max_groups - 1]
        tail = [component for _, items in ranked[max_groups - 1 :] for component in items]
        head.append(("(other)", tail))
        ranked = head

    groups: list[Group] = []
    taken: set[str] = set()
    for key, items in ranked:
        label = {"(core)": "Core", "(other)": "Other modules"}.get(key) or humanize(key)
        group_id = make_id(key.replace("/", "_").strip("()") or "core", taken)
        groups.append(Group(id=group_id, label=clamp_text(label, 40)))
        for component in items:
            component.group_path = group_id
    return groups


# --- driver -----------------------------------------------------------------


def analyze(repo: Repository, meta: ProjectMeta, options: Optional[AnalyzeOptions] = None) -> Analysis:
    options = options or AnalyzeOptions()
    coverage = Coverage(files_total=len(repo.files))

    candidate_files: list[str] = []
    for path in repo.files:
        if not path.endswith((".py", ".pyi")):
            continue
        if is_test_path(path) and not options.include_tests:
            coverage.files_skipped_tests += 1
            continue
        if not is_python_source(path):
            coverage.files_skipped_other += 1
            continue
        if repo.sizes.get(path, 0) > options.max_file_bytes:
            coverage.files_skipped_other += 1
            continue
        candidate_files.append(path)
        if len(candidate_files) >= options.max_files:
            break

    facts: dict[str, FileFacts] = {}
    for path in candidate_files:
        text = repo.read_text(path, options.max_file_bytes)
        if text is None:
            coverage.files_skipped_other += 1
            continue
        parsed = analyze_source(path, text)
        facts[path] = parsed
        if parsed.parsed:
            coverage.files_parsed += 1
        else:
            coverage.files_failed += 1
            coverage.syntax_errors.append((path, parsed.syntax_error or "unparsable"))

    index = build_index(repo, meta.package_roots)
    file_edges = build_file_edges(facts, index, coverage)
    components, depth = assign_components(list(facts), facts, options.depth)
    coverage.depth = depth
    coverage.components_total = len(components)
    component_edges = aggregate_components(file_edges, components)
    ranks = pagerank(components, component_edges)
    score_components(components, component_edges, ranks)

    analysis = Analysis(
        repo=repo,
        meta=meta,
        options=options,
        facts=facts,
        index=index,
        file_edges=file_edges,
        components=components,
        component_edges=component_edges,
        coverage=coverage,
    )
    from .infra import read_infra  # local import: infra is optional decoration

    analysis.infra = read_infra(repo)
    analysis.externals = detect_externals(facts, components, meta, analysis.infra, options)
    actors = detect_actors(facts, components, meta, options)
    graph = build_graph(components, component_edges, analysis.externals, actors, options, repo)
    coverage.components_shown = len([node for node in graph.nodes if node.path])
    analysis.graph = graph
    analysis._actors = actors  # type: ignore[attr-defined]
    return analysis
