"""Repository ingestion.

Three ways in, one result:
  * a local directory,
  * `owner/repo` (shallow `git clone`, tarball from codeload as a fallback),
  * any git URL.

Local/Docker execution is the target runtime, so the whole tree is available;
nothing here is bounded by an LLM context window.

The path filters (`EXCLUDED`, `BARREL`, `MANIFEST`, `SENSITIVE`) are ported from
GitDiagram's `src/server/generate/repository-context.ts` so both versions agree
on what counts as product source.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# --- filters ported from GitDiagram -----------------------------------------

EXCLUDED = re.compile(
    r"(^|/)(?:\.[^/]+|tests?|__tests__|__mocks__|mocks?|testdata|fixtures?|e2e|"
    r"cypress|examples?(?:_src)?|samples?|demos?|stories|storybook|docs?(?:_src)?|"
    r"tutorials?(?:_src)?|documentation|bench|benchmarks?|vendor|third_party|"
    r"node_modules|dist|build|generated|migrations?|alembic|assets|locales?|"
    r"translations?)(/|$)|(?:\.test(?:-d)?|\.spec|\.generated|\.min|\.stories|"
    r"\.designer)\.|(?:_pb2(?:_grpc)?\.py|\.pb(?:\.gw)?\.go|\.g\.cs)$|"
    r"(?:^|/)(?:test\.[^/]+|bench(?:mark|marker)?\.[^/]+|test_[^/]+|"
    r"[^/]+_test\.[^/]+)$",
    re.IGNORECASE,
)
BARREL = re.compile(r"(?:^|/)(?:index|mod|__init__)\.[^/]+$", re.IGNORECASE)
SOURCE = re.compile(
    r"\.(?:py|pyi|[cm]?[jt]sx?|go|rs|java|kt|kts|swift|cs|cpp|cc|c|h|hpp|rb|"
    r"php|ex|exs|scala|clj|vue|svelte|proto|graphql)$",
    re.IGNORECASE,
)
PYTHON_SOURCE = re.compile(r"\.pyi?$", re.IGNORECASE)
MANIFEST = re.compile(
    r"(?:^|/)(?:package\.json|Cargo\.toml|go\.mod|pyproject\.toml|setup\.py|"
    r"setup\.cfg|requirements[^/]*\.txt|Pipfile|build\.gradle(?:\.kts)?|"
    r"mix\.exs|composer\.json|Gemfile|CMakeLists\.txt)$",
    re.IGNORECASE,
)
SENSITIVE = re.compile(
    r"(?:^|/)(?:.*(?:secrets?|credentials?|passwords?|private[_-]?key).*|\.env.*|"
    r".*\.(?:pem|key|p12|pfx))$",
    re.IGNORECASE,
)
# Infrastructure and configuration that carry real architecture signal.
INFRA = re.compile(
    r"(?:^|/)(?:docker-compose[^/]*\.ya?ml|compose\.ya?ml|Dockerfile[^/]*|"
    r"Procfile|Makefile|manage\.py|wsgi\.py|asgi\.py|"
    r".*\.(?:tf|tfvars)|k8s/.*\.ya?ml|helm/.*\.ya?ml|"
    r"\.github/workflows/.*\.ya?ml|tox\.ini|celeryconfig\.py)$",
    re.IGNORECASE,
)

VENDORED_HINTS = re.compile(
    r"(?:^|/)(?:node_modules|site-packages|\.venv|venv|env|__pycache__|"
    r"\.git|\.tox|\.mypy_cache|\.pytest_cache|\.ruff_cache|dist|build|egg-info)(?:/|$)",
    re.IGNORECASE,
)

MAX_FILE_BYTES = 512_000  # parity with GitDiagram's MAX_SOURCE_FILE_BYTES


def is_architecture_source(path: str) -> bool:
    return (
        not EXCLUDED.search(path)
        and not SENSITIVE.search(path)
        and not VENDORED_HINTS.search(path)
        and bool(SOURCE.search(path) or MANIFEST.search(path))
    )


def is_python_source(path: str) -> bool:
    return (
        not EXCLUDED.search(path)
        and not SENSITIVE.search(path)
        and not VENDORED_HINTS.search(path)
        and bool(PYTHON_SOURCE.search(path))
    )


def is_test_path(path: str) -> bool:
    return bool(EXCLUDED.search(path))


# --- repository model -------------------------------------------------------


@dataclass
class Repository:
    """A loaded repository plus the metadata needed to build GitHub links."""

    owner: Optional[str]
    name: str
    branch: str
    root: Path
    files: list[str] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)
    path_types: dict[str, str] = field(default_factory=dict)
    origin: str = "local"
    commit: Optional[str] = None
    cloned_into: Optional[Path] = None
    readme_path: Optional[str] = None
    readme: str = ""
    truncated: bool = False

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}" if self.owner else self.name

    @property
    def file_tree(self) -> set[str]:
        """Every blob and tree path, exactly what GitDiagram validates against."""
        return set(self.path_types)

    def github_url(self, path: str) -> Optional[str]:
        if not self.owner:
            return None
        kind = self.path_types.get(path) or (
            "blob" if "." in path.rsplit("/", 1)[-1] else "tree"
        )
        encoded = "/".join(_quote(part) for part in path.split("/"))
        branch = "/".join(_quote(part) for part in self.branch.split("/"))
        return f"https://github.com/{_quote(self.owner)}/{_quote(self.name)}/{kind}/{branch}/{encoded}"

    def read_text(self, path: str, limit: int = MAX_FILE_BYTES) -> Optional[str]:
        try:
            raw = (self.root / path).read_bytes()[:limit]
        except OSError:
            return None
        if b"\x00" in raw[:8192]:
            return None
        return raw.decode("utf-8", errors="replace")


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _walk(root: Path) -> tuple[list[str], dict[str, int], dict[str, str]]:
    files: list[str] = []
    sizes: dict[str, int] = {}
    types: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in {".git", "__pycache__", "node_modules", ".venv", "venv"}
        )
        if rel_dir != ".":
            types[rel_dir] = "tree"
            for part_index in range(1, len(rel_dir.split("/")) + 1):
                types["/".join(rel_dir.split("/")[:part_index])] = "tree"
        for name in sorted(filenames):
            if name.endswith(".pyc"):
                continue
            rel = f"{rel_dir}/{name}" if rel_dir != "." else name
            try:
                size = (Path(dirpath) / name).stat().st_size
            except OSError:
                size = 0
            files.append(rel)
            sizes[rel] = size
            types[rel] = "blob"
    return files, sizes, types


def _pick_readme(files: list[str]) -> Optional[str]:
    candidates = [
        path
        for path in files
        if re.fullmatch(r"(?:docs?/)?readme(?:\.[a-z]{2}(?:-[a-z]{2})?)?\.(?:md|mdx|rst|txt)", path, re.IGNORECASE)
    ]
    if not candidates:
        return None

    def rank(path: str) -> tuple[int, int]:
        depth = path.count("/")
        return (0 if path.lower().startswith("readme") else 1, depth)

    return sorted(candidates, key=rank)[0]


def load_local(path: str | Path) -> Repository:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is not a directory")
    files, sizes, types = _walk(root)
    branch = "main"
    commit = None
    if (root / ".git").exists():
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            ).stdout.strip() or "main"
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass
    repo = Repository(
        owner=_detect_github_owner(root),
        name=root.name,
        branch=branch,
        root=root,
        files=files,
        sizes=sizes,
        path_types=types,
        origin=f"local:{root}",
        commit=commit,
    )
    repo.readme_path = _pick_readme(files)
    if repo.readme_path:
        repo.readme = repo.read_text(repo.readme_path, 400_000) or ""
    return repo


def _detect_github_owner(root: Path) -> Optional[str]:
    config = root / ".git" / "config"
    if not config.exists():
        return None
    match = re.search(r"github\.com[:/]([^/\s]+)/", config.read_text(errors="replace"))
    return match.group(1) if match else None


REPO_SPEC = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9._-]+)$")


def parse_spec(spec: str) -> tuple[Optional[str], Optional[str], Optional[str], str]:
    """Return (owner, repo, branch, kind) for a user-supplied target."""
    spec = spec.strip().rstrip("/")
    if spec.startswith(("http://", "https://", "git@", "ssh://", "git://")):
        match = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", spec)
        if match:
            return match.group(1), match.group(2), None, "git"
        return None, None, None, "git"
    match = REPO_SPEC.match(spec)
    if match and not Path(spec).exists():
        owner, name = match.groups()
        branch = None
        if "@" in name:
            name, branch = name.split("@", 1)
        return owner, name, branch, "github"
    return None, None, None, "local"


def load_github(
    owner: str,
    repo: str,
    branch: Optional[str] = None,
    token: Optional[str] = None,
    workdir: Optional[Path] = None,
    max_bytes: int = 400 * 1024 * 1024,
) -> Repository:
    """Shallow clone; fall back to the codeload tarball when git is unusable."""
    workdir = Path(workdir or Path(tempfile.gettempdir()) / "staticdiagram-cache")
    workdir.mkdir(parents=True, exist_ok=True)
    destination = workdir / f"{owner}__{repo}"
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)

    url = f"https://github.com/{owner}/{repo}.git"
    if token:
        url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    command = ["git", "clone", "--depth", "1", "--single-branch", "--quiet"]
    if branch:
        command += ["--branch", branch]
    command += [url, str(destination)]

    try:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        destination = _download_tarball(owner, repo, branch, workdir, max_bytes)
        if destination is None:
            raise RuntimeError(f"could not fetch {owner}/{repo}: {detail}") from error

    loaded = load_local(destination)
    loaded.owner = owner
    loaded.name = repo
    loaded.origin = f"github:{owner}/{repo}"
    loaded.cloned_into = destination
    try:
        loaded.commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=destination,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return loaded


def _download_tarball(
    owner: str,
    repo: str,
    branch: Optional[str],
    workdir: Path,
    max_bytes: int,
) -> Optional[Path]:
    ref = branch or "HEAD"
    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "staticdiagram/1.0", "Accept": "*/*"}
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = response.read(max_bytes + 1)
    except (urllib.error.URLError, OSError, TimeoutError):
        return None
    if len(payload) > max_bytes:
        raise RuntimeError(f"{owner}/{repo} tarball exceeds {max_bytes // 2**20} MB")

    destination = workdir / f"{owner}__{repo}"
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        prefix = members[0].name.split("/", 1)[0] if members else ""
        for member in members:
            relative = member.name[len(prefix) + 1 :] if prefix else member.name
            if not relative:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle, length=1 << 20)
    return destination


def load(spec: str, workdir: Optional[Path] = None, token: Optional[str] = None) -> Repository:
    owner, repo, branch, kind = parse_spec(spec)
    if kind == "github" and owner and repo:
        return load_github(owner, repo, branch, token=token, workdir=workdir)
    if kind == "git":
        destination = Path(workdir or Path(tempfile.gettempdir()) / "staticdiagram-cache")
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / re.sub(r"\W+", "-", spec)[-64:]
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", spec, str(target)],
            capture_output=True,
            text=True,
            timeout=600,
            check=True,
        )
        return load_local(target)
    return load_local(spec)


# --- manifests and project metadata ----------------------------------------


@dataclass
class ProjectMeta:
    """What the packaging metadata says, read without executing anything."""

    name: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    summary: Optional[str] = None
    dependencies: list[str] = field(default_factory=list)
    optional_dependencies: dict[str, list[str]] = field(default_factory=dict)
    console_scripts: dict[str, str] = field(default_factory=dict)
    python_requires: Optional[str] = None
    license: Optional[str] = None
    packages: list[str] = field(default_factory=list)
    package_roots: list[str] = field(default_factory=list)
    source_files: dict[str, str] = field(default_factory=dict)

    def top_dependencies(self, limit: int = 12) -> list[str]:
        return sorted(
            {re.split(r"[\[<>=!~;\s]", dep)[0] for dep in self.dependencies if dep.strip()},
        )[:limit]


def _safe_toml(text: str) -> dict:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover
        return {}
    try:
        return tomllib.loads(text)
    except Exception:
        return {}


def read_project_meta(repo: Repository) -> ProjectMeta:
    meta = ProjectMeta()
    pyproject = repo.read_text("pyproject.toml")
    if pyproject:
        data = _safe_toml(pyproject)
        project = data.get("project", {})
        meta.name = project.get("name") or meta.name
        meta.version = project.get("version") or meta.version
        meta.description = project.get("description") or meta.description
        meta.license = (
            project.get("license")
            if isinstance(project.get("license"), str)
            else (project.get("license") or {}).get("text")
        )
        meta.python_requires = project.get("requires-python")
        deps = project.get("dependencies") or []
        meta.dependencies += [str(item) for item in deps]
        for extra, items in (project.get("optional-dependencies") or {}).items():
            meta.optional_dependencies[extra] = [str(item) for item in items]
        for script, target in (project.get("scripts") or {}).items():
            meta.console_scripts[script] = str(target)
        tool = data.get("tool", {})
        setuptools = tool.get("setuptools", {})
        packages = setuptools.get("packages")
        if isinstance(packages, dict):
            find = packages.get("find", {})
            where = find.get("where") or ["."]
            meta.package_roots += [str(item).strip("/") for item in where if str(item).strip("/") != "."]
            meta.package_roots += [""] if "." in where or not where else []
            if find.get("include"):
                meta.packages += [str(item).rstrip(".*") for item in find["include"]]
        elif isinstance(packages, list):
            meta.packages += [str(item) for item in packages]
        poetry = tool.get("poetry", {})
        if poetry:
            meta.name = meta.name or poetry.get("name")
            meta.description = meta.description or poetry.get("description")
            meta.version = meta.version or poetry.get("version")
            for section in ("dependencies",):
                for key, value in (poetry.get(section) or {}).items():
                    if key.lower() == "python":
                        continue
                    constraint = value if isinstance(value, str) else value.get("version", "")
                    meta.dependencies.append(f"{key}{constraint}")
            for script, target in (poetry.get("scripts") or {}).items():
                meta.console_scripts[script] = str(target)
            packages = poetry.get("packages") or []
            for entry in packages:
                if isinstance(entry, dict) and entry.get("include"):
                    meta.packages.append(str(entry["include"]).strip("/"))
                    if entry.get("from"):
                        meta.package_roots.append(str(entry["from"]).strip("/"))
        hatch = tool.get("hatch", {}).get("build", {}).get("targets", {})
        for target in hatch.values() if isinstance(hatch, dict) else []:
            if isinstance(target, dict) and target.get("sources"):
                sources = target["sources"]
                if isinstance(sources, dict):
                    meta.package_roots += [str(value).strip("/") for value in sources.values()]
                elif isinstance(sources, list):
                    meta.package_roots += [str(value).strip("/") for value in sources]
        pdm = tool.get("pdm", {}).get("build", {})
        if isinstance(pdm.get("package-dir"), str):
            meta.package_roots.append(pdm["package-dir"].strip("/"))

    for requirement_path in [
        path
        for path in repo.files
        if re.match(r"(?:^|/)requirements[^/]*\.txt$", path) and not EXCLUDED.search(path)
    ][:6]:
        text = repo.read_text(requirement_path) or ""
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                meta.dependencies.append(line)
        meta.source_files.setdefault("requirements", requirement_path)

    setup_cfg = repo.read_text("setup.cfg")
    if setup_cfg:
        import configparser

        parser = configparser.ConfigParser()
        try:
            parser.read_string(setup_cfg)
        except Exception:
            parser = None
        if parser is not None:
            if parser.has_section("metadata"):
                meta.name = meta.name or parser["metadata"].get("name")
                meta.description = meta.description or parser["metadata"].get("description")
                meta.license = meta.license or parser["metadata"].get("license")
            if parser.has_section("options"):
                raw = parser["options"].get("install_requires") or ""
                meta.dependencies += [
                    line.strip() for line in raw.splitlines() if line.strip()
                ]
                package_dir = parser["options"].get("package_dir") or ""
                for entry in package_dir.splitlines():
                    if "=" in entry:
                        key, value = entry.split("=", 1)
                        if key.strip() in {"", "."}:
                            stripped = value.strip().strip("/")
                            if stripped and stripped != ".":
                                meta.package_roots.append(stripped)
            if parser.has_section("options.entry_points"):
                raw = parser["options.entry_points"].get("console_scripts") or ""
                for line in raw.splitlines():
                    if "=" in line:
                        name, target = line.split("=", 1)
                        meta.console_scripts[name.strip()] = target.strip()

    setup_py = repo.read_text("setup.py")
    if setup_py:
        for match in re.finditer(r"""["']([\w.\-]+)\s*=\s*([^"']+)["']""", setup_py):
            pass  # entry_points strings are ambiguous; handled below
        for match in re.finditer(
            r"entry_points\s*=\s*\{[^}]*console_scripts[^]]*\]", setup_py, re.DOTALL
        ):
            for item in re.finditer(r"""["']([\w.\-]+)\s*=\s*([\w.]+:?\w*)["']""", match.group(0)):
                meta.console_scripts[item.group(1)] = item.group(2)
        for match in re.finditer(r"install_requires\s*=\s*\[(.*?)\]", setup_py, re.DOTALL):
            meta.dependencies += re.findall(r"""["']([^"']+)["']""", match.group(1))
        for match in re.finditer(r"package_dir\s*=\s*\{([^}]*)\}", setup_py, re.DOTALL):
            for item in re.finditer(r"""["']([^"']*)["']\s*:\s*["']([^"']+)["']""", match.group(1)):
                if item.group(1) in {"", "."}:
                    stripped = item.group(2).strip("/")
                    if stripped and stripped != ".":
                        meta.package_roots.append(stripped)

    if (repo.root / "src").is_dir():
        meta.package_roots.append("src")

    meta.package_roots = sorted({root for root in meta.package_roots if root})
    meta.dependencies = sorted({dep.strip() for dep in meta.dependencies if dep.strip()})
    return meta


def summarize_files(repo: Repository) -> dict[str, int]:
    """Extension histogram over non-vendored files."""
    histogram: dict[str, int] = {}
    for path in repo.files:
        if VENDORED_HINTS.search(path):
            continue
        suffix = Path(path).suffix.lower()
        if suffix:
            histogram[suffix] = histogram.get(suffix, 0) + 1
    return dict(sorted(histogram.items(), key=lambda item: -item[1]))


def stats(repo: Repository) -> dict[str, object]:
    python_files = [path for path in repo.files if PYTHON_SOURCE.search(path)]
    product_python = [path for path in python_files if is_python_source(path)]
    test_python = [path for path in python_files if is_test_path(path)]
    return {
        "files": len(repo.files),
        "python_files": len(python_files),
        "product_python_files": len(product_python),
        "test_python_files": len(test_python),
        "extensions": summarize_files(repo),
        "commit": repo.commit,
        "branch": repo.branch,
        "origin": repo.origin,
    }


def write_manifest(repo: Repository, meta: ProjectMeta, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "repository": {
                    "owner": repo.owner,
                    "name": repo.name,
                    "branch": repo.branch,
                    "commit": repo.commit,
                    "origin": repo.origin,
                    "readme": repo.readme_path,
                },
                "project": {
                    "name": meta.name,
                    "version": meta.version,
                    "description": meta.description,
                    "license": meta.license,
                    "python_requires": meta.python_requires,
                    "dependencies": meta.dependencies,
                    "console_scripts": meta.console_scripts,
                    "package_roots": meta.package_roots,
                    "packages": meta.packages,
                },
                "stats": stats(repo),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
