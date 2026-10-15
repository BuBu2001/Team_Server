"""Module resolution: dotted name -> repository path.

Python imports only become architecture edges once `a.b.c` is known to be
`src/pkg/a/b/c.py` in *this* repository rather than a third-party package. The
index is built from the file tree plus every source root the packaging metadata
declares (`src/` layout, poetry `packages`, setuptools `package_dir`, monorepo
packages with their own `pyproject.toml`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .ingest import Repository
from .pyanalysis import Import

INIT_FILE = "__init__.py"


@dataclass
class ModuleIndex:
    roots: list[str]
    by_module: dict[str, str] = field(default_factory=dict)
    packages: set[str] = field(default_factory=set)
    all_paths: set[str] = field(default_factory=set)

    def add(self, module: str, path: str) -> None:
        existing = self.by_module.get(module)
        if existing is None or len(path) < len(existing):
            self.by_module[module] = path

    def resolve_module(self, module: str) -> Optional[str]:
        if not module:
            return None
        return self.by_module.get(module)

    def resolve(self, module: str, names: Iterable[str] = ()) -> Optional[str]:
        """Prefer a submodule named by `from X import name`, else the module."""
        for name in names:
            candidate = self.resolve_module(f"{module}.{name}") if module else None
            if candidate:
                return candidate
        return self.resolve_module(module)


def detect_roots(repo: Repository, extra_roots: Iterable[str] = ()) -> list[str]:
    """Directories that can start an absolute import path."""
    candidates: set[str] = {""}
    for root in extra_roots:
        cleaned = root.strip("/")
        if cleaned:
            candidates.add(cleaned)
    if (repo.root / "src").is_dir():
        candidates.add("src")

    # Monorepos: any directory with its own packaging file is an import root,
    # as is its `src/`.
    for path in repo.files:
        if re.fullmatch(r".*/(?:pyproject\.toml|setup\.py|setup\.cfg)", path):
            parent = path.rsplit("/", 1)[0]
            if parent and parent.count("/") <= 3:
                candidates.add(parent)
                if f"{parent}/src" in repo.path_types:
                    candidates.add(f"{parent}/src")

    # A directory holding `__init__.py` whose parent has none is a package root.
    python_dirs = {
        path.rsplit("/", 1)[0] for path in repo.files if path.endswith(INIT_FILE) and "/" in path
    }
    for directory in python_dirs:
        parent = directory.rsplit("/", 1)[0] if "/" in directory else ""
        parent_init = f"{parent}/{INIT_FILE}" if parent else ""
        if parent_init and parent_init in repo.path_types:
            continue
        if parent not in python_dirs and directory.count("/") <= 2:
            candidates.add(directory)

    ordered = sorted(candidates, key=lambda item: (item != "", item.count("/"), item))
    return ordered


def build_index(repo: Repository, roots: Optional[list[str]] = None) -> ModuleIndex:
    roots = roots or detect_roots(repo)
    index = ModuleIndex(roots=roots, all_paths=set(repo.files))
    python_files = [path for path in repo.files if path.endswith((".py", ".pyi"))]

    for root in roots:
        prefix = f"{root}/" if root else ""
        for path in python_files:
            if prefix and not path.startswith(prefix):
                continue
            relative = path[len(prefix) :]
            if not relative or relative.startswith("."):
                continue
            parts = relative.split("/")
            if parts[-1] == INIT_FILE:
                module_parts = parts[:-1]
            elif parts[-1].endswith(".pyi"):
                module_parts = parts[:-1] + [parts[-1][:-4]]
            else:
                module_parts = parts[:-1] + [parts[-1][:-3]]
            if not module_parts:
                continue
            module = ".".join(module_parts)
            if module in index.by_module and len(index.by_module[module]) <= len(path):
                continue
            index.by_module.setdefault(module, path)
            for depth in range(1, len(module_parts)):
                index.packages.add(".".join(module_parts[:depth]))
            if parts[-1] == INIT_FILE:
                index.packages.add(module)

    # Namespace directories (no __init__.py) still resolve as packages.
    directories = {
        path.rsplit("/", 1)[0] for path in python_files if "/" in path
    }
    for directory in directories:
        for root in roots:
            prefix = f"{root}/" if root else ""
            if prefix and not directory.startswith(prefix):
                continue
            relative = directory[len(prefix) :]
            if not relative:
                continue
            module = relative.replace("/", ".")
            index.packages.add(module)
    return index


def package_of(path: str) -> str:
    """The dotted package a file belongs to (`a/b/c.py` -> `a.b`)."""
    parts = path.split("/")
    if parts[-1] == INIT_FILE:
        parts = parts[:-1]
    else:
        parts = parts[:-1]
    return ".".join(parts)


def root_relative(index: ModuleIndex, path: str) -> str:
    """Strip the source root, so `src/pkg/mod.py` becomes `pkg/mod.py`.

    Module names in the index are relative to a root; a relative import's base
    package must be computed the same way or `from . import x` never resolves.
    """
    for root in sorted(index.roots, key=len, reverse=True):
        if root and path.startswith(f"{root}/"):
            return path[len(root) + 1 :]
    return path


def resolve_import(
    index: ModuleIndex, item: Import, from_path: str
) -> Optional[str]:
    """Turn one import statement into a repository path, or None."""
    if item.level:
        relative = root_relative(index, from_path)
        base_parts = relative.split("/")
        if base_parts[-1] == INIT_FILE:
            base_parts = base_parts[:-1]
        else:
            base_parts = base_parts[:-1]
        climb = item.level - 1
        if climb > len(base_parts):
            return None
        if climb:
            base_parts = base_parts[:-climb]
        base_module = ".".join(base_parts)
        target = f"{base_module}.{item.module}" if item.module else base_module
        resolved = index.resolve(target, item.names)
        if resolved:
            return resolved
        # `from . import sibling` where sibling is a module inside the package.
        for name in item.names:
            candidate = f"{base_module}.{name}" if base_module else name
            found = index.resolve_module(candidate)
            if found:
                return found
        return None

    if not item.module:
        return None
    resolved = index.resolve(item.module, item.names)
    if resolved:
        return resolved
    # The module may exist only as a directory (a namespace package without an
    # importable file). Map it to the package's `__init__.py` when there is one.
    parts = item.module.split(".")
    for depth in range(len(parts), 0, -1):
        prefix = "/".join(parts[:depth])
        for root in index.roots:
            candidate = f"{root}/{prefix}/{INIT_FILE}" if root else f"{prefix}/{INIT_FILE}"
            if candidate in index.all_paths:
                return candidate
    return None


def unresolved_top_level(index: ModuleIndex, item: Import) -> Optional[str]:
    """The third-party (or stdlib) root an import points at, if not in-repo."""
    if item.level or not item.module:
        return None
    if resolve_import(index, item, ""):
        return None
    return item.top or None
