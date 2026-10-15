"""Infrastructure and configuration facts, without executing anything.

`docker-compose.yml`, `Dockerfile`, CI workflows and `.env.example` prove
runtime dependencies that imports alone cannot show (a Postgres the code only
reaches through a URL, a queue a worker consumes). Parsing is deliberately
tolerant: there is no YAML dependency, only line scans that look for the keys
that matter.
"""

from __future__ import annotations

import re
from pathlib import Path

from .ingest import Repository

SERVICE_IMAGES = {
    "postgres": "PostgreSQL",
    "postgis": "PostgreSQL",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "redis": "Redis",
    "valkey": "Redis",
    "mongo": "MongoDB",
    "kafka": "Kafka",
    "redpanda": "Kafka",
    "rabbitmq": "RabbitMQ",
    "nats": "NATS",
    "elasticsearch": "Elasticsearch",
    "opensearch": "OpenSearch",
    "minio": "MinIO (S3)",
    "clickhouse": "ClickHouse",
    "influxdb": "InfluxDB",
    "neo4j": "Neo4j",
    "cassandra": "Cassandra",
    "zookeeper": "ZooKeeper",
    "memcached": "Memcached",
    "localstack": "AWS (LocalStack)",
    "mailhog": "SMTP (MailHog)",
    "smtp": "SMTP",
}

COMPOSE_FILE = re.compile(r"(?:^|/)(?:docker-)?compose[^/]*\.ya?ml$", re.IGNORECASE)
DOCKERFILE = re.compile(r"(?:^|/)Dockerfile[^/]*$", re.IGNORECASE)
WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$", re.IGNORECASE)
ENV_EXAMPLE = re.compile(r"(?:^|/)\.env\.(?:example|sample|template)$", re.IGNORECASE)


def _compose_files(repo: Repository) -> list[str]:
    return [path for path in repo.files if COMPOSE_FILE.search(path)][:4]


def parse_compose(text: str, path: str) -> list[dict]:
    services: list[dict] = []
    current: dict | None = None
    in_services = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0:
            in_services = stripped.startswith("services:")
            current = None
            continue
        if not in_services:
            continue
        if indent <= 2 and stripped.endswith(":") and ":" not in stripped[:-1]:
            current = {"name": stripped[:-1].strip("'\""), "image": "", "evidence": path}
            services.append(current)
            continue
        if current is not None and indent >= 4:
            match = re.match(r"image:\s*['\"]?([^'\"#]+)", stripped)
            if match:
                current["image"] = match.group(1).strip()
    return services


def parse_dockerfile(text: str, path: str) -> dict:
    entrypoint: list[str] = []
    base: list[str] = []
    exposed: list[str] = []
    installs: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("FROM "):
            base.append(line.split()[1])
        elif line.startswith(("CMD ", "ENTRYPOINT ")):
            entrypoint.append(re.sub(r"^(CMD|ENTRYPOINT)\s+", "", line))
        elif line.startswith("EXPOSE "):
            exposed.extend(line.split()[1:])
        elif re.match(r"RUN .*(pip install|uv pip|poetry install|apt-get install)", line):
            installs.append(line[:160])
    return {
        "path": path,
        "base": base,
        "entrypoint": entrypoint,
        "expose": exposed,
        "installs": installs[:8],
    }


def parse_workflow(text: str, path: str) -> dict:
    jobs: list[str] = []
    steps: list[str] = []
    triggers: list[str] = []
    in_jobs = False
    in_on = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0:
            in_jobs = stripped.startswith("jobs:")
            in_on = stripped in {"on:", "true:"} or stripped.startswith("on:")
            continue
        if in_jobs and indent == 2 and stripped.endswith(":"):
            jobs.append(stripped[:-1])
        if in_jobs and stripped.startswith("- name:"):
            steps.append(stripped.split(":", 1)[1].strip().strip("'\"")[:80])
        if in_jobs and stripped.startswith("runs-on:"):
            continue
        if in_on and indent <= 2:
            triggers.append(stripped.rstrip(":"))
    return {"path": path, "jobs": jobs[:12], "steps": steps[:20], "triggers": triggers[:8]}


def parse_env_example(text: str, path: str) -> list[str]:
    keys: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        key = re.sub(r"^export\s+", "", key)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            keys.append(key)
    return keys


def read_infra(repo: Repository) -> dict:
    services: list[dict] = []
    known: set[str] = set()
    for path in _compose_files(repo):
        text = repo.read_text(path, 200_000) or ""
        for service in parse_compose(text, path):
            if service["name"] in known:
                continue
            known.add(service["name"])
            services.append(service)

    dockerfiles = [
        {
            **parse_dockerfile(repo.read_text(path, 200_000) or "", path),
        }
        for path in repo.files
        if DOCKERFILE.search(path)
    ][:4]

    workflows = [
        parse_workflow(repo.read_text(path, 200_000) or "", path)
        for path in repo.files
        if WORKFLOW.search(path)
    ][:6]

    env_keys: list[str] = []
    env_path: str | None = None
    for path in repo.files:
        if ENV_EXAMPLE.search(path):
            env_path = path
            env_keys = parse_env_example(repo.read_text(path, 100_000) or "", path)
            break

    system_services = []
    for service in services:
        image = service["image"].lower()
        for needle, label in SERVICE_IMAGES.items():
            if needle in image:
                system_services.append(
                    {**service, "system": label, "matched": needle}
                )
                break

    makefile = None
    if "Makefile" in repo.path_types:
        text = repo.read_text("Makefile", 100_000) or ""
        targets = re.findall(r"^([A-Za-z0-9_.\-]+):", text, re.MULTILINE)
        makefile = {"path": "Makefile", "targets": sorted(set(targets))[:24]}

    return {
        "services": services,
        "system_services": system_services,
        "dockerfiles": dockerfiles,
        "workflows": workflows,
        "env_example": {"path": env_path, "keys": env_keys} if env_path else None,
        "makefile": makefile,
        "infra_paths": [
            path for path in repo.files if INFRA_LIKE.search(path)
        ][:20],
    }


INFRA_LIKE = re.compile(
    r"(?:^|/)(?:docker-compose[^/]*\.ya?ml|compose\.ya?ml|Dockerfile[^/]*|Procfile|"
    r"Makefile|manage\.py|wsgi\.py|asgi\.py|\.github/workflows/[^/]+\.ya?ml|"
    r"[^/]+\.tf|Chart\.ya?ml|k8s/[^/]+\.ya?ml)$",
    re.IGNORECASE,
)
