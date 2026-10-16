"""Единый сетевой протокол системы GettingStarted (Host <-> Pi).

Транспорт — length-prefixed JSON поверх TCP (без внешних зависимостей).
Через него связаны все модули системы:

  * передача файлов   — upload / download / list_files (файлы Pi уходят на Host);
  * диаграммы         — analyze (gitdiagram-static), tree_diagram / render_png
                        (app_platform.diagram_gen / image_gen);
  * слежение за работой — log_action / history (журнал в SQLite app_platform.db).

Протокол версионирован; команды описаны в COMMANDS.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import zipfile
from pathlib import Path
from typing import Any, Optional

PROTOCOL_VERSION = 2
DEFAULT_HOST_PORT = 47823

# Все команды, которые понимает хост (полный список — в обработчике host_server).
COMMANDS = [
    "ping", "hello", "bye", "quit",
    "upload", "download", "list_files",
    "analyze", "tree_diagram", "render_png",
    "log_action", "history",
]

MAX_CHUNK = 1 << 20          # 1 MiB на кадр бинарных данных
MAX_MESSAGE = 64 << 20       # предохранитель на размер JSON-сообщения


# ------------------------------------------------------------- базовые примитивы

def send_msg(sock: socket.socket, obj: dict[str, Any]) -> None:
    """Отправка JSON-сообщения: 4-байтовый big-endian заголовок + тело."""
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sock.sendall(len(data).to_bytes(4, "big") + data)


def recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_msg(sock: socket.socket) -> Optional[dict[str, Any]]:
    header = recv_exact(sock, 4)
    if header is None:
        return None
    (length,) = struct.unpack("!I", header)
    if length > MAX_MESSAGE:
        raise ValueError(f"Слишком большое сообщение: {length} байт")
    body = recv_exact(sock, length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def send_chunk(sock: socket.socket, data: bytes) -> None:
    """Бинарный кадр (файл/архив/PNG): заголовок с длиной + данные."""
    sock.sendall(len(data).to_bytes(4, "big") + data)


def recv_chunk(sock: socket.socket) -> Optional[bytes]:
    header = recv_exact(sock, 4)
    if header is None:
        return None
    (length,) = struct.unpack("!I", header)
    if length > MAX_MESSAGE:
        raise ValueError(f"Слишком большой блок данных: {length} байт")
    return recv_exact(sock, length)


def send_file(sock: socket.socket, path: Path) -> None:
    with open(path, "rb") as fh:
        while True:
            part = fh.read(MAX_CHUNK)
            if not part:
                break
            send_chunk(sock, part)
    send_chunk(sock, b"")  # пустой кадр = конец файла


def recv_file(sock: socket.socket, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(dest, "wb") as fh:
        while True:
            part = recv_chunk(sock)
            if not part:
                break
            fh.write(part)
            total += len(part)
    return total


# ------------------------------------------------------------------ безопасность

def safe_join(root: Path, rel: str) -> Path:
    """Защищённый путь внутри root (без ..-выходов и абсолютных путей)."""
    root = root.resolve()
    candidate = (root / str(rel).strip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Недопустимый путь: {rel}")
    return candidate


def zip_tree(src_dir: Path, zip_path: Path) -> None:
    """Упаковать дерево папок в zip (для загрузки целых проектов)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(src_dir))


def unzip_safely(zip_path: Path, dest_dir: Path) -> int:
    """Распаковать zip с проверкой путей (защита от zip-slip)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            target = safe_join(dest_dir, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                out.write(src.read())
            count += 1
    return count


# --------------------------------------------------------------------- окружение

def add_project_src_to_path() -> None:
    """Добавляет gitdiagram-static/src и корень репозитория в sys.path.

    Благодаря этому консоли GettingStarted работают как единая система без
    pip install: хост видит и staticdiagram (диаграммы кода), и app_platform
    (журнал работы, mermaid по дереву, PNG).
    """
    import sys

    here = Path(__file__).resolve().parent
    repo = here.parent
    for cand in (repo / "gitdiagram-static" / "src", repo):
        if cand.is_dir() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))


def host_storage_dir(port: int) -> Path:
    """Папка хоста для файлов Pi — ОБЩАЯ с веб-панелью (data/).

    Хост складывает файлы Pi в ``<repo>/data/files``, а проекты в
    ``<repo>/data/projects`` — те же каталоги, что использует веб-панель
    (app_platform.main.UPLOAD_DIR/PI_FILES_DIR). Благодаря этому веб и консоль
    работают с одними данными: проект, отправленный Pi из консоли, сразу виден
    и анализируется на веб-панели, и наоборот.

    ``GETTINGSTARTED_STORAGE`` переопределяет базу (используется тестами).
    """
    base = Path(os.environ.get("GETTINGSTARTED_STORAGE",
                               str(Path(__file__).resolve().parent.parent / "data")))
    base.mkdir(parents=True, exist_ok=True)
    return base
