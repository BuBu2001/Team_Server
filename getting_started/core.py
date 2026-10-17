"""Общая логика консоли GettingStarted (используется Windows- и Linux-версии).

Это ЯДРО единой системы, в которой связаны все модули проекта:

  * gitdiagram-static (``staticdiagram``) — хост строит mermaid/PNG/SVG-диаграммы
    присланных Pi проектов;
  * app_platform.diagram_gen / image_gen — диаграмма по дереву файлов и её PNG;
  * app_platform.db — журнал работы (SQLite): кто, что и когда делал;
  * protocol.py — единый TCP-протокол передачи файлов между Pi и Host.

Сценарий:
  1. Два пользователя запускают консоль GettingStarted (на своих ПК).
  2. Приветствие: "Приветствуем! Выберите режимы работы".
  3. Host — сервер: хранит файлы Pi, строит диаграммы, ведёт журнал.
     Pi   — обычный пользователь: отправляет файлы/проекты хосту, смотрит
     их диаграммы, скачивает обратно, следит за своей работой.
  4. Host видит "Ждите пользователя Pi", Pi видит "Ждите пользователя Host".
  5. Когда оба дождались — сессия установлена и начинается совместная работа
    (меню команд, всё по локальной сети между двумя компьютерами).
"""

from __future__ import annotations

import io
import json
import os
import select
import socket
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

# Единый протокол системы (send_msg/recv_msg, файлы, zip, пути).
if __package__:
    from . import protocol as proto
else:
    import protocol as proto

APP_NAME = "GettingStarted"
DEFAULT_HOST_PORT = proto.DEFAULT_HOST_PORT
LINE = "=" * 62

MODES = {
    "1": ("host", "Host - Это для тех, кто будет хостом. "
                  "Хост выполняет все функции которые нужны для работы."),
    "2": ("pi",   "Pi   - это обычный пользователь."),
}


# ------------------------------------------------------------------ ввод/вывод

def clear_screen() -> None:
    if os.name == "nt":
        os.system("cls")
    else:
        os.system("clear")


def print_banner() -> None:
    print(LINE)
    print(f"  {APP_NAME} — локальная система Host ⇄ Pi")
    print(LINE)


def choose_mode(ask: Callable[[str], str]) -> str:
    """Встречает пользователя и возвращает выбранный режим: 'host' или 'pi'."""
    print_banner()
    print("Приветствуем! Выберите режимы работы:\n")
    for key, (_, description) in MODES.items():
        print(f"  [{key}] {description}")
    print()
    while True:
        raw = ask("Введите 1 (Host) или 2 (Pi): ").strip().lower()
        choice = {"1": "host", "2": "pi", "host": "host", "pi": "pi"}.get(raw)
        if choice:
            return choice
        print("Неверный выбор. Введите 1 (Host) или 2 (Pi).")


def local_ip() -> str:
    """IP машины в локальной сети (для подсказки пользователю Pi)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ----------------------------------------------------------- рабочие модули системы

def load_modules(notify: Callable[[str], None]) -> dict[str, Any]:
    """Загружает все модули системы; отсутствующие — деградируют мягко.

    Перед загрузкой обязательно расширяем sys.path (корень репозитория +
    gitdiagram-static/src) — иначе при обычном запуске скрипта из любой папки
    ``app_platform`` не находится, и консоль молча работает без журнала и
    древовидных диаграмм, хотя модули проекта на месте.
    """
    proto.add_project_src_to_path()
    mods: dict[str, Any] = {"staticdiagram": None, "diagram_gen": None,
                            "image_gen": None, "db": None}
    try:
        from staticdiagram import generate as sd_generate  # type: ignore
        mods["staticdiagram"] = sd_generate
    except Exception:
        notify("Модуль gitdiagram-static не найден — анализ кода будет ограничен.")
    try:
        from app_platform import diagram_gen, image_gen  # type: ignore
        mods["diagram_gen"], mods["image_gen"] = diagram_gen, image_gen
    except Exception:
        notify("Модули app_platform не найдены — древовидные диаграммы будут ограничены.")
    try:
        from app_platform import db as pdb  # type: ignore
        pdb.init_db()
        mods["db"] = pdb
    except Exception:
        notify("База данных журнала недоступна — история действий вестись не будет.")
    return mods


def journal(mods: dict, user: str, action: str, details: str = "",
            project: str = "") -> None:
    """Запись в журнал работы (app_platform.db); без БД — тихо пропускаем."""
    db = mods.get("db")
    if db is None:
        return
    try:
        db.log_action(user, action, details, project)
    except Exception:
        pass


def history(mods: dict, user: Optional[str] = None, limit: int = 50) -> list[dict]:
    db = mods.get("db")
    if db is None:
        return []
    try:
        return db.list_actions(user=user, limit=limit)
    except Exception:
        return []


def capture_stdout(fn: Callable[..., Any], *args, **kwargs) -> tuple[Any, str]:
    """Вызывает функцию и возвращает (результат, перехваченный stdout)."""
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        res = fn(*args, **kwargs)
    finally:
        sys.stdout = old
    return res, buf.getvalue()


def build_tree(src: Path, max_depth: int = 4) -> str:
    """Дерево папок в формате gitingest (├──/└──), для diagram_gen.tree_to_mermaid."""
    lines: list[str] = [f"└── {src.name or src}/"]

    def walk(d: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        entries = sorted(
            [p for p in d.iterdir()
             if p.name not in {".git", "__pycache__", "node_modules", ".venv"}],
            key=lambda p: (p.is_file(), str(p).lower()))
        for i, p in enumerate(entries):
            last = i == len(entries) - 1
            conn, ext = ("└── ", "    ") if last else ("├── ", "│   ")
            if p.is_dir():
                lines.append(f"{prefix}{conn}{p.name}/")
                walk(p, prefix + ext, depth + 1)
            else:
                lines.append(f"{prefix}{conn}{p.name}")

    if src.is_dir():
        walk(src, "", 1)
    return "\n".join(lines)


# --------------------------------------------------------------- функции Хоста

class HostWorkspace:
    """Хранилище хоста: файлы и проекты, присланные пользователями Pi."""

    def __init__(self, root: Path):
        self.root = root
        (root / "files").mkdir(parents=True, exist_ok=True)
        (root / "projects").mkdir(parents=True, exist_ok=True)

    def rel_list(self, sub: str) -> list[dict]:
        base = self.root / sub
        out = []
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out.append({"name": str(p.relative_to(base)),
                            "size": p.stat().st_size,
                            "mtime": int(p.stat().st_mtime)})
        return out

    def path_for(self, sub: str, name: str) -> Path:
        return proto.safe_join(self.root / sub, name)

    def resolve_project(self, name: str) -> Optional[Path]:
        """Найти проект по имени: 'src.zip', 'src', папка или zip — всё верно."""
        base = self.root / "projects"
        raw = proto.safe_join(base, name.strip("/")) if name else base
        cands = [raw]
        if raw.suffix == ".zip":
            cands.append(raw.with_suffix(""))
        else:
            cands.append(Path(str(raw) + ".zip"))
        for c in cands:
            if c.is_dir() or c.is_file():
                return c
        return None


def host_handle_request(conn: socket.socket, req: dict, ws: HostWorkspace,
                        mods: dict, peer: str,
                        notify: Callable[[str], None]) -> bool:
    """Обрабатывает один запрос Pi. Возвращает False, если соединение закрыто."""
    cmd = req.get("type", "")
    user = str(req.get("user", "pi"))
    db = mods.get("db")

    def ok(payload: Optional[dict] = None) -> None:
        msg: dict[str, Any] = {"type": "ok", "cmd": cmd}
        if payload:
            msg.update(payload)
        proto.send_msg(conn, msg)

    def err(message: str) -> None:
        proto.send_msg(conn, {"type": "error", "cmd": cmd, "message": message})

    try:
        if cmd == "quit":
            proto.send_msg(conn, {"type": "bye"})
            journal(mods, user, "disconnect", f"Pi отключился ({peer})", "")
            return False

        if cmd == "ping":
            ok({"version": proto.PROTOCOL_VERSION,
                "commands": proto.COMMANDS, "host": socket.gethostname()})
            return True

        # ---------------- передача файлов: Pi -> Host (хост = сервер) ----------
        if cmd == "upload":
            kind = req.get("kind", "file")
            name = str(req.get("name", "unnamed"))
            sub = "projects" if kind == "project_zip" else "files"
            dest = ws.path_for(sub, name)
            if kind == "project_zip":
                tmp = dest.with_suffix(".tmp.zip")
                size = proto.recv_file(conn, tmp)
                target_dir = dest.parent / dest.stem
                count = proto.unzip_safely(tmp, target_dir)
                tmp.unlink(missing_ok=True)
                ok({"stored_as": str(dest.relative_to(ws.root)),
                    "files": count, "bytes": size})
                notify(f"Pi прислал проект '{dest.stem}' ({count} файлов, {size} байт)")
                # В журнал/веб-панель попадает именно распакованная папка проекта:
                # так веб-анализ (gitingest/gitdiagram) работает без ошибок,
                # а не спотыкается о путь к .zip-файлу.
                if db is not None:
                    try:
                        db.register_project(user, dest.stem, str(target_dir))
                    except Exception:
                        pass
                journal(mods, user, "upload", f"{kind}: {name}", dest.stem)
            else:
                size = proto.recv_file(conn, dest)
                ok({"stored_as": str(dest.relative_to(ws.root)), "bytes": size})
                notify(f"Pi прислал файл '{name}' ({size} байт)")
                journal(mods, user, "upload", f"{kind}: {name}", name)
            return True

        # ---------------- download: Host -> Pi ---------------------------------
        if cmd == "download":
            sub = req.get("sub", "files")
            name = str(req.get("name", ""))
            p = ws.path_for(sub, name)
            if sub == "projects" and not p.exists():
                rp = ws.resolve_project(name)
                if rp is not None:
                    p = rp
            if p.is_dir():
                z = p.parent / f"{p.name}.download.zip"
                proto.zip_tree(p, z)
                proto.send_msg(conn, {"type": "ok", "cmd": cmd,
                                      "is_dir": True, "size": z.stat().st_size})
                proto.send_file(conn, z)
                z.unlink(missing_ok=True)
            elif p.is_file():
                proto.send_msg(conn, {"type": "ok", "cmd": cmd,
                                      "is_dir": False, "size": p.stat().st_size})
                proto.send_file(conn, p)
            else:
                err(f"Файл не найден на хосте: {sub}/{name}")
                return True
            notify(f"Pi скачал '{name}'")
            journal(mods, user, "download", name, name)
            return True

        if cmd == "list_files":
            ok({"files": ws.rel_list("files"), "projects": ws.rel_list("projects")})
            journal(mods, user, "list_files", "", "")
            return True

        # ---------------- диаграммы (gitdiagram-static + app_platform) ---------
        if cmd in ("analyze", "tree_diagram", "render_png"):
            name = str(req.get("name", ""))
            src = ws.resolve_project(name)
            if src is None:
                err(f"Проект '{name}' не найден на хосте. Сначала отправьте его (up-dir).")
                return True
            proj_dir = src if src.is_dir() else (
                ws.root / "projects" / src.stem if (ws.root / "projects" / src.stem).is_dir() else None)
            if proj_dir is None:  # zip без распакованной папки — распакуем временно
                proj_dir = src.parent / (src.stem + ".unpacked")
                proto.unzip_safely(src, proj_dir)

            if cmd == "analyze":
                text, fmt = "", "mermaid"
                if mods["staticdiagram"] is not None:
                    generate = mods["staticdiagram"]
                    res = None
                    err_msg = None
                    try:
                        code, out = capture_stdout(
                            generate, str(proj_dir), formats=["mermaid"])
                        res = code
                    except Exception as exc:  # noqa: BLE001
                        err_msg = str(exc).splitlines()[0] if str(exc) else repr(exc)
                    candidate = str(getattr(res, "mermaid", "") or "")
                    has_nodes = (
                        mods["diagram_gen"] is not None
                        and mods["diagram_gen"].mermaid_has_nodes(candidate)
                    )
                    if candidate.strip() and has_nodes:
                        text = candidate
                    else:
                        if err_msg:
                            notify(f"gitdiagram-static: {err_msg} — fallback на дерево")
                        elif not candidate.strip():
                            notify("gitdiagram-static не вернул диаграмму — fallback на дерево")
                        else:
                            notify("gitdiagram-static не нашёл связей в коде — "
                                   "fallback на диаграмму по дереву")
                        if req.get("fallback") != "off" and mods["diagram_gen"]:
                            fmt = "mermaid-tree"
                            tree = build_tree(proj_dir)
                            text = mods["diagram_gen"].tree_to_mermaid(tree)
                elif mods["diagram_gen"]:
                    fmt = "mermaid-tree"
                    text = mods["diagram_gen"].tree_to_mermaid(build_tree(proj_dir))
                if not text:
                    err("gitdiagram-static не вернул диаграмму (не git-репозиторий?)."
                        " Попробуйте tree_diagram.")
                    return True
                ok({"format": fmt, "text": text, "name": name})
                notify(f"Pi запросил диаграмму кода проекта '{name}'")
                journal(mods, user, "analyze", name, name)
                return True

            if not mods["diagram_gen"]:
                err("app_platform.diagram_gen недоступен на хосте.")
                return True
            tree = build_tree(proj_dir)
            mermaid = mods["diagram_gen"].tree_to_mermaid(tree)
            if cmd == "tree_diagram":
                ok({"format": "mermaid", "text": mermaid, "name": name})
                notify(f"Pi запросил древовидную диаграмму '{name}'")
                journal(mods, user, "tree_diagram", name, name)
                return True

            # render_png — модуль app_platform.image_gen рисует дерево в PNG
            if not mods["image_gen"]:
                err("app_platform.image_gen недоступен на хосте.")
                return True
            png_bytes, _code = capture_stdout(
                mods["image_gen"].tree_to_image,
                tree, f"Проект: {name}", "структура проекта (GettingStarted)")
            data = bytes(png_bytes) if isinstance(png_bytes, (bytes, bytearray)) else b""
            if not data:
                err("Не удалось построить PNG.")
                return True
            proto.send_msg(conn, {"type": "ok", "cmd": cmd, "size": len(data),
                                  "name": f"{name}.png"})
            proto.send_chunk(conn, data)
            proto.send_chunk(conn, b"")  # пустой кадр = конец файла (терминатор)
            notify(f"Pi получил PNG-диаграмму проекта '{name}' ({len(data)} байт)")
            journal(mods, user, "render_png", name, name)
            return True

        # ---------------- журнал работы ----------------------------------------
        if cmd == "log_action":
            journal(mods, user, str(req.get("action", "note")),
                    str(req.get("details", "")), str(req.get("project", "")))
            ok()
            return True

        if cmd == "history":
            rows = history(mods, user=None, limit=int(req.get("limit", 50)))
            ok({"rows": rows, "me": history(mods, user, limit=100)})
            return True

        err(f"Неизвестная команда: {cmd}")
        return True
    except Exception as exc:  # noqa: BLE001
        notify(f"Ошибка обработки '{cmd}': {exc}")
        traceback.print_exc()
        try:
            err(f"Ошибка хоста: {exc}")
        except OSError:
            return False
    return True


def make_host_handler(mods: dict, notify: Callable[[str], None],
                      port: int) -> Callable[[socket.socket], None]:
    """Все функции хоста: приём файлов, диаграммы (gitdiagram-static +
    app_platform), журнал работы. Один обработчик на всю сессию пары.

    Handshake hello/hello-ack уже выполнен в wait_for_peer — здесь сразу
    начинаем принимать команды Pi."""

    ws = HostWorkspace(proto.host_storage_dir(port))

    def handle(conn: socket.socket, hello: Optional[dict] = None) -> None:
        # Handshake: клиент Pi всегда шлёт {"type":"hello","role":"pi"} первым
        # сообщением соединения. Если вызывающий код уже прочитал его
        # (wait_for_peer), передаём hello сюда — тогда отвечаем ack и сразу
        # переходим к командам. Если hello=None — читаем hello сами и шлём ack.
        req = None
        if hello is None:
            try:
                first = proto.recv_msg(conn)
            except Exception:
                first = None
            if not first or first.get("type") != "hello" or first.get("role") != "pi":
                notify("Подключился не Pi — соединение закрыто.")
                return
            send_hello_ack(conn)
        else:
            if hello.get("type") != "hello" or hello.get("role") != "pi":
                notify("Подключился не Pi — соединение закрыто.")
                return
            send_hello_ack(conn)
        notify("Пользователь Pi подключён. Сессия установлена.")
        peer = f"{conn.getpeername()[0]}:{conn.getpeername()[1]}"
        first_req = None
        with conn:
            while True:
                req = first_req if first_req is not None else proto.recv_msg(conn)
                first_req = None
                if req is None:
                    notify("Пользователь Pi отключился. Сессия завершена.")
                    return
                if not host_handle_request(conn, req, ws, mods, peer, notify):
                    # Pi корректно завершил сессию командой quit
                    notify("Пользователь Pi отключился. Сессия завершена.")
                    return

    return handle


def run_host_direct(
    port: int,
    timeout: float,
    notify: Callable[[str], None],
    ask_fn: Callable[[str], str] = input,
) -> int:
    """Хост без лишнего двойного handshake: ожидание Pi → сразу рабочие функции."""
    mods = load_modules(notify)
    notify(f"Порт: {port}. IP для пользователя Pi: {local_ip()}")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    srv.settimeout(timeout if timeout > 0 else None)
    try:
        notify("Ждите пользователя Pi...")
        try:
            conn, addr = srv.accept()
        except (TimeoutError, OSError):
            notify("Таймаут ожидания. Пользователь Pi не подключился.")
            return 1
    finally:
        srv.close()
    notify(f"Пользователь Pi найден ({addr[0]}:{addr[1]}).")
    # Handshake выполняется внутри обработчика — ровно один раз.
    make_host_handler(mods, notify, port)(conn)
    return 0


# --------------------------------------------------------- ожидание пары (Host/Pi)

def wait_for_peer(
    mode: str,
    port: int,
    timeout: float,
    notify: Callable[[str], None],
) -> Optional[socket.socket]:
    """Блокирующий «Ждите пользователя …» для обеих ролей."""
    deadline = time.time() + timeout if timeout > 0 else None
    if mode == "host":
        notify("Ждите пользователя Pi...")
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(1)
        srv.settimeout(timeout if timeout > 0 else None)
        try:
            conn, addr = srv.accept()
        except (TimeoutError, OSError):
            notify("Таймаут ожидания. Пользователь Pi не подключился.")
            srv.close()
            return None
        finally:
            srv.close()
        notify(f"Пользователь Pi найден ({addr[0]}:{addr[1]}).")
        # Handshake выполняет ОДИН раз вызывающий код (run_host_direct или
        # run_host_session) — здесь его нет, иначе hello читается дважды и
        # вторая попытка получает уже команду Pi вместо handshake.
        return conn

    # --- pi ---
    notify("Ждите пользователя Host...")
    host_ip = os.environ.get("GETTINGSTARTED_HOST_IP", "").strip()
    if not host_ip and sys.stdin.isatty():
        while not host_ip:
            host_ip = input(
                f"Введите IP адрес хоста (или empty для {local_ip()}): "
            ).strip()
            if not host_ip:
                host_ip = local_ip()
    elif not host_ip:
        host_ip = "127.0.0.1"
    attempt = 0
    while True:
        attempt += 1
        try:
            conn = socket.create_connection((host_ip, port), timeout=5)
            break
        except OSError:
            if deadline and time.time() >= deadline:
                notify("Таймаут ожидания. Пользователь Host не найден.")
                return None
            if attempt % 3 == 1:
                notify(f"Хост {host_ip}:{port} ещё не запущен. Продолжаем ждать...")
            time.sleep(2)
    proto.send_msg(conn, {"type": "hello", "role": "pi"})
    ack = proto.recv_msg(conn)
    if not ack or ack.get("role") != "host":
        notify("Ответ хоста не получен — соединение закрыто.")
        conn.close()
        return None
    notify("Пользователь Host найден. Сессия установлена.")
    return conn


def run_host_session(
    conn: socket.socket,
    on_pair: Callable[[socket.socket], None],
    notify: Callable[[str], None],
) -> None:
    hello = proto.recv_msg(conn)
    if not hello or hello.get("role") != "pi":
        notify("Подключился не Pi — соединение закрыто.")
        conn.close()
        return
    send_hello_ack(conn)
    notify("Пользователь Pi подключён. Сессия установлена.")
    on_pair(conn)


def send_hello_ack(conn: socket.socket) -> None:
    """Ответ хоста на handshake Pi (используется и wait_for_peer, и run_host_session)."""
    proto.send_msg(conn, {"type": "hello-ack", "role": "host"})


# --------------------------------------------------------------------- клиент Pi

def ask(inline: Optional[str], prompt: str, ask_fn: Callable[[str], str]) -> str:
    """Возвращает inline-значение (для скриптов/тестов) либо спрашивает пользователя."""
    if inline is not None:
        print(f"{prompt}{inline}")
        return inline
    return ask_fn(prompt).strip()


def pi_send_file(conn: socket.socket, path_str: str, as_project: bool,
                 notify: Callable[[str], None]) -> None:
    p = Path(path_str).expanduser()
    if as_project:
        if not p.is_dir():
            notify("Для проекта укажите путь к папке.")
            return
        tmp = Path(os.environ.get("TMP", "/tmp")) / f"{p.name}.gs.zip"
        proto.zip_tree(p, tmp)
        proto.send_msg(conn, {"type": "upload", "kind": "project_zip",
                              "name": f"{p.name}.zip", "user": _PI_USER})
        proto.send_file(conn, tmp)
        tmp.unlink(missing_ok=True)
    else:
        if not p.is_file():
            notify("Укажите путь к существующему файлу.")
            return
        proto.send_msg(conn, {"type": "upload", "kind": "file",
                              "name": p.name, "user": _PI_USER})
        proto.send_file(conn, p)
    resp = proto.recv_msg(conn)
    if resp and resp.get("type") == "ok":
        notify(f"Отправлено на хост: {resp.get('stored_as', p.name)} "
               f"({resp.get('bytes', '?')} байт)")
    else:
        notify(f"Хост отклонил загрузку: {(resp or {}).get('message')}")


def pi_recv_binary(conn: socket.socket) -> tuple[Optional[dict], Optional[bytes]]:
    meta = proto.recv_msg(conn)
    if not meta or meta.get("type") != "ok":
        return meta, None
    data = b""
    while True:
        part = proto.recv_chunk(conn)
        if not part:
            break
        data += part
    return meta, data


def pi_download(conn: socket.socket, sub: str, name: str, dest_str: str,
                notify: Callable[[str], None]) -> None:
    proto.send_msg(conn, {"type": "download", "sub": sub, "name": name,
                          "user": _PI_USER})
    meta, data = pi_recv_binary(conn)
    if meta is None:
        notify("Нет ответа хоста.")
        return
    if meta.get("type") == "error":
        notify(f"Ошибка хоста: {meta.get('message')}")
        return
    dest = Path(dest_str).expanduser()
    if meta.get("is_dir") and not str(dest).endswith(".zip"):
        dest = dest.with_suffix(".zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data or b"")
    notify(f"Сохранено: {dest} ({len(data or b'')} байт)")


_PI_USER = os.environ.get("GETTINGSTARTED_USER", "pi")


def run_pi_client(conn: socket.socket, notify: Callable[[str], None],
                  ask_fn: Callable[[str], str], script: Optional[list[str]] = None,
                  auto: Optional[dict] = None) -> None:
    """Интерфейс обычного пользователя Pi: файлы, диаграммы, журнал — всё через хост."""
    auto = auto or {}
    with conn:
        if script is not None:  # автоматический режим (тесты)
            commands = script
        elif auto.get("auto_demo"):
            commands = ["demo", "hist", "quit"]
        else:
            commands = None

        def next_cmd() -> Optional[str]:
            if commands is not None:
                if not commands:
                    return ""
                c = commands.pop(0)
                print(f">>> {c}")
                return c
            # ВАЖНО: не приводим всю строку к нижнему регистру — иначе ломаются
            # пути и имена с заглавными буквами. Регистр гасим только у команды.
            return input(MENU_PROMPT).strip()

        MENU_PROMPT = ("Команда (up-file/up-dir/dl/list/analyz/tree/png/hist/"
                       "note/demo/quit) > ")
        if commands is None:
            notify("\nДоступные команды Pi:\n"
                   "  up-file <путь>   — отправить файл на хост\n"
                   "  up-dir  <путь>   — отправить проект (папку) на хост\n"
                   "  analyz  <имя>    — диаграмма кода (gitdiagram-static на хосте)\n"
                   "  tree    <имя>    — mermaid-диаграмма структуры проекта\n"
                   "  png     <имя> <ф | -> — PNG-диаграмма (сохранить или показать размер)\n"
                   "  dl      <files|projects> <имя> <куда>\n"
                   "                     — скачать файл/проект с хоста\n"
                   "  list             — список файлов и проектов на хосте\n"
                   "  hist             — журнал работы (мой и общий)\n"
                   "  note  <текст>    — записать действие в журнал\n"
                   "  demo             — полный цикл: up-dir→analyz→tree→png→hist\n"
                   "  quit             — отключиться")

        while True:
            try:
                cmd = next_cmd()
            except (EOFError, KeyboardInterrupt):
                cmd = "quit"
            if cmd.strip().lower() in ("", "quit", "exit", "q"):
                proto.send_msg(conn, {"type": "quit", "user": _PI_USER})
                proto.recv_msg(conn)
                notify("Отключение от хоста. Пока!")
                return
            parts = cmd.split(maxsplit=2)
            head = parts[0].lower() if parts else ""
            try:
                if head == "up-file":
                    pi_send_file(conn, parts[1] if len(parts) > 1 else "", False, notify)
                elif head == "up-dir":
                    pi_send_file(conn, parts[1] if len(parts) > 1 else "", True, notify)
                elif head == "list":
                    proto.send_msg(conn, {"type": "list_files", "user": _PI_USER})
                    r = proto.recv_msg(conn) or {}
                    notify("Файлы на хосте: " + (", ".join(f['name'] for f in r.get('files', [])) or "—"))
                    notify("Проекты на хосте: " + (", ".join(f['name'] for f in r.get('projects', [])) or "—"))
                elif head in ("analyz", "analyze", "tree"):
                    name = parts[1] if len(parts) > 1 else ""
                    proto.send_msg(conn, {"type": ("analyze" if head.startswith("analyz")
                                                   else "tree_diagram"),
                                          "name": name, "user": _PI_USER})
                    r = proto.recv_msg(conn) or {}
                    if r.get("type") == "error":
                        notify(f"Ошибка хоста: {r.get('message')}")
                    else:
                        notify(f"--- Диаграмма ({r.get('format')}) проекта "
                               f"'{r.get('name')}' ---")
                        print(r.get("text", ""))
                        notify("--- конец диаграммы ---")
                elif head == "png":
                    name = parts[1] if len(parts) > 1 else ""
                    rest = parts[2].strip() if len(parts) > 2 else "-"
                    proto.send_msg(conn, {"type": "render_png", "name": name,
                                          "user": _PI_USER})
                    meta, data = pi_recv_binary(conn)
                    if meta and meta.get("type") == "error":
                        notify(f"Ошибка хоста: {meta.get('message')}")
                    elif data:
                        if rest in ("", "-"):
                            notify(f"PNG получен: {len(data)} байт (имя: {meta.get('name')})")
                        else:
                            Path(rest).write_bytes(data)
                            notify(f"PNG сохранён в {rest} ({len(data)} байт)")
                elif head == "dl":
                    if len(parts) < 3:
                        notify("Использование: dl <files|projects> <имя> <куда>")
                    else:
                        sub, name, dest = parts[1].lower(), parts[2], ""
                        # имя и путь могут быть склеены пробелом — разбиваем максимально
                        tokens = cmd.split(maxsplit=3)
                        if len(tokens) == 4:
                            sub, name, dest = tokens[1].lower(), tokens[2], tokens[3]
                        else:
                            dest = "-"
                        if dest == "-":
                            notify("Укажите путь назначения третьим аргументом.")
                        else:
                            pi_download(conn, sub, name, dest, notify)
                elif head == "hist":
                    proto.send_msg(conn, {"type": "history", "user": _PI_USER,
                                          "limit": 30})
                    r = proto.recv_msg(conn) or {}
                    notify("Моя история:")
                    for row in r.get("me", [])[:15]:
                        notify(f"  [{row.get('ts')}] {row.get('user')} — "
                               f"{row.get('action')} {row.get('details', '')}")
                    notify("Общий журнал хоста (последние):")
                    for row in r.get("rows", [])[:10]:
                        notify(f"  [{row.get('ts')}] {row.get('user')} — "
                               f"{row.get('action')} {row.get('details', '')}")
                elif head == "note":
                    proto.send_msg(conn, {"type": "log_action", "user": _PI_USER,
                                          "action": "note",
                                          "details": cmd[len(head):].strip()})
                    proto.recv_msg(conn)
                    notify("Записано в журнал.")
                elif head == "demo":
                    here = Path(__file__).resolve().parent
                    proj = auto.get("demo_dir") or str(here.parent / "getting_started")
                    commands_backup = commands
                    commands = [f"up-dir {Path(proj).resolve()}",
                                "list",
                                f"analyz {Path(proj).name}.zip",
                                f"tree {Path(proj).name}.zip",
                                f"png {Path(proj).name}.zip -",
                                f"dl projects {Path(proj).name}.zip "
                                f"{os.environ.get('TMP', '/tmp')}/{Path(proj).name}_back.zip",
                                "hist", "quit"]
                    notify("Демо-цикл: отправка проекта → диаграммы → скачивание → журнал")
                    del commands_backup
                else:
                    notify("Неизвестная команда. Наберите 'demo' или 'quit'.")
            except IndexError:
                notify("Недостаточно аргументов для команды.")
            except OSError as exc:
                notify(f"Соединение с хостом потеряно: {exc}")
                return
