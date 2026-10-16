"""Локальная часть репозитория cryless — аналог ``.git`` в текущем проекте.

Структура в корне проекта::

    .cryless/config.json     имя репозитория, адрес сервера, ник, токен
    .crylessignore           что не отправлять на сервер (синтаксис .gitignore)

Команды cryless работают только в корне проекта: каталог ``.cryless``
создаётся именно там, и все файлы этого каталога уходят на сервер как проект.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .errors import CrylessError
from .naming import looks_ascii_clean

DIR_NAME = ".cryless"
CONFIG_NAME = "config.json"
IGNORE_NAME = ".crylessignore"

#: Файлы больше — не отправляем (аналог лимита gitingest на сервере).
MAX_FILE_BYTES = 10 * 1024 * 1024
#: Общий размер архива.
MAX_TOTAL_BYTES = 256 * 1024 * 1024

#: Что игнорируется по умолчанию (как .gitignore).
DEFAULT_IGNORE = """\
# cryless: что НЕ отправлять на сервер (синтаксис как в .gitignore)
.cryless/
.git/
.venv/
venv/
env/
__pycache__/
*.pyc
*.pyo
*.log
.env
.idea/
.vscode/
node_modules/
dist/
build/
*.zip
*.tar.gz
"""


@dataclass
class RepoConfig:
    """Содержимое .cryless/config.json."""

    name: str
    server: str
    user: str = ""
    token: str = ""
    project_id: int | None = None
    created_at: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "server": self.server,
            "user": self.user,
            "token": self.token,
            "project_id": self.project_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RepoConfig":
        return cls(
            name=str(data.get("name", "")),
            server=str(data.get("server", "")),
            user=str(data.get("user", "")),
            token=str(data.get("token", "")),
            project_id=data.get("project_id"),
            created_at=str(data.get("created_at", "")),
        )


@dataclass
class PackResult:
    """Итог сборки архива проекта."""

    archive: Path
    files: int
    size: int
    skipped: list[tuple[str, str]] = field(default_factory=list)


def repo_dir(root: Path) -> Path:
    return root / DIR_NAME


def config_path(root: Path) -> Path:
    return repo_dir(root) / CONFIG_NAME


def load_config(root: Path) -> RepoConfig | None:
    path = config_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise CrylessError(f"Повреждён {path}: {e}") from e
    return RepoConfig.from_dict(data)


def save_config(root: Path, config: RepoConfig) -> Path:
    directory = repo_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)  # в конфиге лежит токен доступа
    except OSError:
        pass
    path = config_path(root)
    path.write_text(
        json.dumps(config.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def write_default_ignore(root: Path) -> Path | None:
    """Создаёт .crylessignore, если его ещё нет (существующий не трогаем)."""
    path = root / IGNORE_NAME
    if path.exists():
        return None
    path.write_text(DEFAULT_IGNORE, encoding="utf-8")
    return path


def find_repo_root(start: Path) -> Path | None:
    """Ближайший каталог вверх по дереву, где есть .cryless/config.json."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if config_path(candidate).is_file():
            return candidate
    return None


def ensure_project_root(root: Path) -> None:
    """Условие «команда запускается в корне проекта».

    Отсекаем случаи, когда это явно не проект: пустой каталог, корень
    файловой системы, домашний каталог, вложенная папка уже созданного
    репозитория cryless.
    """
    root = root.resolve()
    if root == Path(root.anchor):
        raise CrylessError(
            "Это корень файловой системы, а не проект.\n"
            "  Перейдите в папку проекта: cd ~/my_project"
        )
    if root == Path.home():
        raise CrylessError(
            "Это домашний каталог, а не корень проекта.\n"
            "  Перейдите в папку проекта: cd ~/my_project"
        )
    parent_repo = find_repo_root(root.parent)
    if parent_repo is not None:
        raise CrylessError(
            f"Вы не в корне проекта: выше уже есть репозиторий cryless ({parent_repo}).\n"
            f"  Запустите команду в корне проекта: cd {parent_repo}"
        )
    entries = [p for p in root.iterdir() if p.name != DIR_NAME]
    if not entries:
        raise CrylessError(
            "Каталог пуст — это не похоже на корень проекта.\n"
            "  Положите файлы проекта и повторите команду в его корне."
        )


def project_root_warning(root: Path) -> str:
    """Предупреждение, если имя папки проекта содержит пробелы или кириллицу."""
    name = root.resolve().name
    if looks_ascii_clean(name):
        return ""
    return (
        f"имя папки проекта «{name}» содержит пробелы или кириллицу — "
        "для сервера лучше переименовать папку в латиницу (например my_project)"
    )


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _new_pathspec(lines: list[str]):
    """Создаёт PathSpec: «gitignore» в новых pathspec, «gitwildmatch» в старых."""
    import pathspec

    try:
        return pathspec.PathSpec.from_lines("gitignore", lines)
    except (KeyError, ValueError):  # старая версия pathspec
        return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def load_ignore_spec(root: Path):
    """Собирает правила .crylessignore (+ встроенные обязательные)."""
    lines: list[str] = []
    ignore_file = root / IGNORE_NAME
    if ignore_file.is_file():
        lines += ignore_file.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        lines += DEFAULT_IGNORE.splitlines()
    # Обязательные правила: конфиг с токеном и сам архив никогда не отправляем.
    lines += [f"{DIR_NAME}/", "*.cryless.tmp.zip"]
    return _new_pathspec(lines)


def pack_repo(root: Path) -> PackResult:
    """Собирает zip-архив проекта, соблюдая .crylessignore и лимиты."""
    root = root.resolve()
    spec = load_ignore_spec(root)
    skipped: list[tuple[str, str]] = []

    fd, tmp_name = tempfile.mkstemp(prefix="cryless-", suffix=".cryless.tmp.zip")
    os.close(fd)
    archive = Path(tmp_name)
    files = 0
    total = 0
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirnames, filenames in os.walk(root):
                here = Path(dirpath)
                rel_dir = _relative(here, root) if here != root else ""
                # не спускаемся в игнорируемые каталоги
                dirnames[:] = [
                    d
                    for d in sorted(dirnames)
                    if not spec.match_file(f"{rel_dir}/{d}/" if rel_dir else f"{d}/")
                ]
                for fname in sorted(filenames):
                    path = here / fname
                    rel = _relative(path, root)
                    if spec.match_file(rel):
                        skipped.append((rel, "по .crylessignore"))
                        continue
                    if path.is_symlink():
                        skipped.append((rel, "символическая ссылка"))
                        continue
                    try:
                        size = path.stat().st_size
                    except OSError as e:
                        skipped.append((rel, f"недоступен: {e}"))
                        continue
                    if size > MAX_FILE_BYTES:
                        skipped.append((rel, f"больше {MAX_FILE_BYTES // (1024 * 1024)} МБ"))
                        continue
                    if total + size > MAX_TOTAL_BYTES:
                        raise CrylessError(
                            f"Проект больше {MAX_TOTAL_BYTES // (1024 * 1024)} МБ — "
                            "добавьте лишнее в .crylessignore"
                        )
                    zf.write(path, rel)
                    files += 1
                    total += size
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    return PackResult(archive=archive, files=files, size=archive.stat().st_size, skipped=skipped)


def cleanup_pack(result: PackResult) -> None:
    try:
        result.archive.unlink(missing_ok=True)
    except OSError:
        pass


def clear_repo_metadata(root: Path) -> None:
    """Удаляет .cryless (нужно при пересоздании репозитория)."""
    shutil.rmtree(repo_dir(root), ignore_errors=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{int(value)} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"
