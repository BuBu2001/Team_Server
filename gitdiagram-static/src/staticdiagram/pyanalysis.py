"""Per-file facts, extracted with `ast` (no third-party parsers).

This module replaces what GitDiagram asks a model to do: it reads the source and
records what is actually there — imports, entry points, route decorators, ORM
models, queue tasks, the drivers a file talks to, and the environment variables
it reads. Everything downstream is derived from these facts, so every claim in
the final diagram has a file and a line behind it.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Optional

MAX_FILE_BYTES = 512_000

# module name -> architecture signal
SIGNALS: dict[str, tuple[str, ...]] = {
    "fastapi": ("web:fastapi", "http_server"),
    "starlette": ("web:starlette", "http_server"),
    "flask": ("web:flask", "http_server"),
    "django": ("web:django", "http_server"),
    "tornado": ("web:tornado", "http_server"),
    "sanic": ("web:sanic", "http_server"),
    "bottle": ("web:bottle", "http_server"),
    "pyramid": ("web:pyramid", "http_server"),
    "falcon": ("web:falcon", "http_server"),
    "litestar": ("web:litestar", "http_server"),
    "quart": ("web:quart", "http_server"),
    "aiohttp": ("http_client", "web:aiohttp"),
    "sqlalchemy": ("orm:sqlalchemy", "state:sql"),
    "sqlmodel": ("orm:sqlmodel", "state:sql"),
    "tortoise": ("orm:tortoise", "state:sql"),
    "peewee": ("orm:peewee", "state:sql"),
    "databases": ("orm:databases", "state:sql"),
    "alembic": ("migrations", "state:sql"),
    "psycopg": ("driver:postgres", "state:sql"),
    "psycopg2": ("driver:postgres", "state:sql"),
    "asyncpg": ("driver:postgres", "state:sql"),
    "pymysql": ("driver:mysql", "state:sql"),
    "MySQLdb": ("driver:mysql", "state:sql"),
    "sqlite3": ("driver:sqlite", "state:sql"),
    "pymongo": ("driver:mongo", "state:document_db"),
    "motor": ("driver:mongo", "state:document_db"),
    "redis": ("driver:redis", "state:cache"),
    "aioredis": ("driver:redis", "state:cache"),
    "memcache": ("driver:memcached", "state:cache"),
    "celery": ("queue:celery", "background"),
    "rq": ("queue:rq", "background"),
    "dramatiq": ("queue:dramatiq", "background"),
    "arq": ("queue:arq", "background"),
    "huey": ("queue:huey", "background"),
    "kafka": ("queue:kafka", "streaming"),
    "confluent_kafka": ("queue:kafka", "streaming"),
    "pika": ("queue:rabbitmq", "streaming"),
    "aio_pika": ("queue:rabbitmq", "streaming"),
    "nats": ("queue:nats", "streaming"),
    "boto3": ("cloud:aws", "object_storage"),
    "botocore": ("cloud:aws", "object_storage"),
    "google": ("cloud:gcp",),
    "azure": ("cloud:azure",),
    "minio": ("object_storage",),
    "click": ("cli:click",),
    "typer": ("cli:typer",),
    "argparse": ("cli:argparse",),
    "fire": ("cli:fire",),
    "requests": ("http_client",),
    "httpx": ("http_client",),
    "urllib3": ("http_client",),
    "jinja2": ("templates",),
    "mako": ("templates",),
    "grpc": ("rpc:grpc",),
    "websockets": ("realtime:websocket",),
    "socketio": ("realtime:socketio",),
    "openai": ("ai:openai",),
    "anthropic": ("ai:anthropic",),
    "transformers": ("ai:ml",),
    "torch": ("ai:ml",),
    "sklearn": ("ai:ml",),
    "langchain": ("ai:orchestration",),
    "prometheus_client": ("observability:metrics",),
    "opentelemetry": ("observability:tracing",),
    "structlog": ("observability:logging",),
    "sentry_sdk": ("observability:errors",),
    "stripe": ("external:payments",),
    "twilio": ("external:sms",),
    "pydantic": ("schema:pydantic",),
    "marshmallow": ("schema:marshmallow",),
    "attrs": ("schema:attrs",),
    "yaml": ("config:yaml",),
    "toml": ("config:toml",),
    "tomllib": ("config:toml",),
    "git": ("vcs:gitpython",),
    "docker": ("infra:docker",),
    "kubernetes": ("infra:kubernetes",),
    "terraform": ("infra:terraform",),
}

# Standard library modules are never repository edges and never "external APIs".
STDLIB = set(getattr(__import__("sys"), "stdlib_module_names", set()))

ROUTE_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "route",
    "websocket",
    "api_route",
    "task",
    "command",
}

ORM_BASES = {
    "Base",
    "DeclarativeBase",
    "Model",
    "SQLModel",
    "Entity",
    "Tabular",
    "DeclarativeMeta",
}


@dataclass
class Import:
    module: str  # dotted, as written (relative imports keep the level separate)
    level: int = 0
    names: tuple[str, ...] = ()
    lineno: int = 0
    kind: str = "import"  # import | from | dynamic | string

    @property
    def top(self) -> str:
        return self.module.split(".")[0] if self.module else ""


@dataclass
class ClassInfo:
    name: str
    bases: tuple[str, ...]
    lineno: int
    docstring: Optional[str] = None
    decorators: tuple[str, ...] = ()


@dataclass
class FunctionInfo:
    name: str
    lineno: int
    decorators: tuple[str, ...] = ()
    docstring: Optional[str] = None
    is_async: bool = False


@dataclass
class FileFacts:
    path: str
    loc: int = 0
    parsed: bool = True
    docstring: Optional[str] = None
    imports: list[Import] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    entrypoint: bool = False
    routes: list[tuple[str, int]] = field(default_factory=list)  # (verb, lineno)
    tasks: list[tuple[str, int]] = field(default_factory=list)
    commands: list[tuple[str, int]] = field(default_factory=list)
    signals: set[str] = field(default_factory=set)
    env_vars: set[str] = field(default_factory=set)
    third_party: set[str] = field(default_factory=set)
    defines_app: bool = False
    has_orm_models: bool = False
    syntax_error: Optional[str] = None

    @property
    def is_package_init(self) -> bool:
        return self.path.endswith("__init__.py")

    def signal_groups(self) -> dict[str, set[str]]:
        grouped: dict[str, set[str]] = {}
        for signal in self.signals:
            kind, _, value = signal.partition(":")
            grouped.setdefault(kind, set()).add(value or kind)
        return grouped


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    if isinstance(current, ast.Call):
        return _dotted(current.func)
    return ".".join(reversed(parts))


def _first_line(docstring: Optional[str]) -> Optional[str]:
    if not docstring:
        return None
    line = docstring.strip().splitlines()[0] if docstring.strip() else ""
    line = re.sub(r"\s+", " ", line).strip()
    return line[:200] or None


_IMPORT_RE = re.compile(r"^\s*from\s+(\.*)([\w.]+)\s+import", re.MULTILINE)
_PLAIN_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)


def analyze_source(path: str, text: str) -> FileFacts:
    facts = FileFacts(path=path, loc=text.count("\n") + 1)
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError, RecursionError) as error:
        facts.parsed = False
        facts.syntax_error = str(error).splitlines()[0][:160]
        for match in _IMPORT_RE.finditer(text):
            facts.imports.append(
                Import(module=match.group(2), level=len(match.group(1)), lineno=1, kind="from")
            )
        for match in _PLAIN_IMPORT_RE.finditer(text):
            facts.imports.append(Import(module=match.group(1), lineno=1, kind="import"))
        _collect_imported_names(facts)
        return facts

    facts.docstring = _first_line(ast.get_docstring(tree))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.imports.append(
                    Import(module=alias.name, lineno=node.lineno, kind="import")
                )
        elif isinstance(node, ast.ImportFrom):
            facts.imports.append(
                Import(
                    module=node.module or "",
                    level=node.level or 0,
                    names=tuple(alias.name for alias in node.names),
                    lineno=node.lineno,
                    kind="from",
                )
            )
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in {"importlib.import_module", "__import__"} and node.args:
                argument = node.args[0]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    facts.imports.append(
                        Import(module=argument.value, lineno=node.lineno, kind="dynamic")
                    )
            if name in {"os.getenv", "os.environ.get"} and node.args:
                argument = node.args[0]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    facts.env_vars.add(argument.value)
            if name in {"uvicorn.run", "app.run", "serve"} and node.args:
                facts.defines_app = True
        elif isinstance(node, ast.Subscript):
            value = _dotted(node.value)
            if value == "os.environ" and isinstance(node.slice, ast.Constant):
                if isinstance(node.slice.value, str):
                    facts.env_vars.add(node.slice.value)
        elif isinstance(node, ast.ClassDef):
            bases = tuple(_dotted(base) for base in node.bases if _dotted(base))
            info = ClassInfo(
                name=node.name,
                bases=bases,
                lineno=node.lineno,
                docstring=_first_line(ast.get_docstring(node)),
                decorators=tuple(
                    _dotted(decorator) for decorator in node.decorator_list if _dotted(decorator)
                ),
            )
            facts.classes.append(info)
            if any(base.rsplit(".", 1)[-1] in ORM_BASES for base in bases):
                facts.has_orm_models = True
                facts.signals.add("orm:model")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorators = tuple(
                _dotted(decorator) for decorator in node.decorator_list if _dotted(decorator)
            )
            facts.functions.append(
                FunctionInfo(
                    name=node.name,
                    lineno=node.lineno,
                    decorators=decorators,
                    docstring=_first_line(ast.get_docstring(node)),
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                )
            )
            for decorator in decorators:
                owner, _, method = decorator.rpartition(".")
                tail = method.split("(")[0]
                if tail in ROUTE_METHODS and owner:
                    owner_tail = owner.rsplit(".", 1)[-1]
                    if owner_tail in {"app", "router", "blueprint", "bp", "api", "application", "site", "server"} or tail == "route":
                        facts.routes.append((tail.upper() if tail != "route" else "ROUTE", node.lineno))
                # A Celery/RQ task is background work; a click/typer command is
                # a CLI surface. Mixing them mislabels both.
                if tail in {"task", "shared_task", "celery_task"}:
                    facts.tasks.append((node.name, node.lineno))
                elif tail in {"command", "group"} and owner:
                    facts.commands.append((node.name, node.lineno))
            if node.name in {"main", "run", "cli", "app", "create_app"}:
                facts.defines_app = facts.defines_app or node.name in {"main", "run", "create_app"}
        elif isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and any(
                    isinstance(comparator, ast.Constant) and comparator.value == "__main__"
                    for comparator in test.comparators
                )
            ):
                facts.entrypoint = True
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"app", "application", "celery_app", "api"}:
                    facts.defines_app = True

    _collect_imported_names(facts)
    return facts


def _collect_imported_names(facts: FileFacts) -> None:
    for item in facts.imports:
        if not item.module:
            continue
        top = item.top
        if item.level == 0 and top and top not in STDLIB:
            facts.third_party.add(top)
        for signal in SIGNALS.get(top, ()):
            facts.signals.add(signal)
        # Nested namespaces such as `google.cloud.storage`.
        if top in {"google", "azure", "github", "gitlab"}:
            parts = item.module.split(".")
            for depth in range(2, min(len(parts), 4) + 1):
                for signal in SIGNALS.get(".".join(parts[:depth]), ()):
                    facts.signals.add(signal)
    if facts.entrypoint:
        facts.signals.add("entrypoint:script")
    if facts.routes:
        facts.signals.add("http_server")
    if facts.tasks:
        facts.signals.add("background")
    if facts.commands:
        facts.signals.add("cli")


def analyze_repository_file(path: str, text: Optional[str]) -> Optional[FileFacts]:
    if text is None:
        return None
    if b"\x00" in text[:4096].encode("utf-8", "ignore"):
        return None
    return analyze_source(path, text)


def read_facts(path, text: str) -> FileFacts:  # pragma: no cover - convenience
    return analyze_source(str(path), text)
