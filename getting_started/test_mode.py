"""Тестовый режим: Host и Pi в ОДНОМ процессе — проверка всей системы на одной машине.

Обычно система работает на двух машинах: Host (сервер) принимает файлы, строит
диаграммы и ведёт журнал, а Pi (обычный пользователь) отправляет свои файлы и
проекты. Для проверки нужен второй компьютер — это неудобно.

Тестовый режим совмещает обе роли:

  * хост-сервер поднимается в фоновом потоке этого же процесса;
  * роль Pi в этом же процессе проходит полный рабочий цикл:
    файл → проект → диаграмма кода → mermaid-дерево → PNG → скачивание → журнал;
  * в конце печатается отчёт «что проверено» (✓/×) и понятный вывод
    TEST MODE PASS / FAIL.

Запуск:
    ./run.sh demo                                  # Host + Pi вместе
    ./run.sh demo --with-web                       # и проверка веб-панели
    python3 getting_started/gettingstarted_linux.py --mode test

По умолчанию результаты пишутся в общее хранилище (data/), поэтому после теста
присланный проект и журнал видно на веб-панели. Флаг ``--test-isolated``
использует временную папку и временную БД (ничего рабочего не трогает).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# Единое ядро системы: тот же код, что работает в реальных ролях Host/Pi.
if __package__:
    from . import core
    from . import protocol as proto
else:
    import core
    import protocol as proto

#: Имя тестового проекта (совпадает с именем папки образца).
SAMPLE_PROJECT_NAME = "test_project"

#: Небольшой детерминированный проект с перекрёстными импортами: статический
#: анализ (gitdiagram-static) находит связи и строит настоящую диаграмму кода.
SAMPLE_FILES: dict[str, str] = {
    "README.md": "# test_project\n\nОбразец для тестового режима (Host + Pi).\n",
    "src/test_project/__init__.py": 'from . import orders, report\n\n__all__ = ["orders", "report"]\n',
    "src/test_project/storage.py": 'def load():\n    return [{"price": 10}, {"price": 32}]\n',
    "src/test_project/orders.py": 'from . import storage\n\n\ndef total(items=None):\n    items = storage.load() if items is None else items\n    return sum(i["price"] for i in items)\n',
    "src/test_project/report.py": 'from . import orders\n\n\ndef summary():\n    return f"Итого: {orders.total()}"\n',
    "apps/main.py": 'from test_project import report\n\n\nif __name__ == "__main__":\n    print(report.summary())\n',
}


@dataclass
class Check:
    """Один пункт отчёта тестового режима."""

    name: str
    ok: bool
    detail: str = ""


class _Tee:
    """Дублирует вывод: пишет в реальный stdout и запоминает текст.

    Клиент Pi печатает тело диаграммы напрямую в stdout (``print``), а не через
    notify-коллбэк — поэтому для проверок нужен ещё и перехват stdout.
    """

    def __init__(self, real):
        self._real = real
        self.lines: list[str] = []

    def write(self, text: str) -> int:
        self.lines.append(text)
        return self._real.write(text)

    def flush(self) -> None:
        self._real.flush()

    def isatty(self) -> bool:  # некоторые библиотеки это спрашивают
        return getattr(self._real, "isatty", lambda: False)()


def build_sample_project(root: Path) -> Path:
    """Создаёт образец проекта для тестового цикла и возвращает его папку."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in SAMPLE_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _separator(notify: Callable[[str], None], text: str) -> None:
    notify("")
    notify("=" * 62)
    notify(text)
    notify("=" * 62)


def _print_report(checks: list[Check], notify: Callable[[str], None]) -> int:
    passed = sum(1 for c in checks if c.ok)
    notify("")
    _separator(notify, f"ТЕСТОВЫЙ РЕЖИМ: пройдено {passed} из {len(checks)}")
    for check in checks:
        mark = "✓" if check.ok else "×"
        line = f"  [{mark}] {check.name}"
        if check.detail:
            line += f" — {check.detail}"
        notify(line)
    notify("")
    if passed == len(checks):
        notify("TEST MODE PASS: Host и Pi работают вместе "
               "(файлы, проект, диаграммы, PNG, скачивание, журнал)")
        return 0
    failed = [c.name for c in checks if not c.ok]
    notify(f"TEST MODE FAIL: не пройдено — {', '.join(failed)}")
    return 1


# ------------------------------------------------------------------ веб-панель

def _web_alive(base_url: str, timeout: float = 1.5) -> bool:
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base_url}/api/cryless/ping")
        return resp.status_code == 200
    except Exception:
        return False


def _start_web(web_port: int, notify: Callable[[str], None]):
    """Поднимает веб-панель в фоне (или переиспользует уже запущенную).

    Возвращает (base_url, stop) — stop() нужен, только если сервер подняли мы.
    """
    base_url = f"http://127.0.0.1:{web_port}"
    if _web_alive(base_url):
        notify(f"[Web]  сервер уже запущен: {base_url}")
        return base_url, None
    try:
        import uvicorn

        from app_platform.main import app
    except Exception as exc:  # noqa: BLE001
        notify(f"[Web]  веб-панель недоступна: {exc}")
        return None, None

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=web_port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, name="gs-web", daemon=True)
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline:
        if _web_alive(base_url):
            notify(f"[Web]  веб-панель запущена: {base_url}")

            def stop() -> None:
                server.should_exit = True
                thread.join(timeout=10)

            return base_url, stop
        time.sleep(0.2)
    notify("[Web]  веб-панель не поднялась за 20 с")
    server.should_exit = True
    return None, None


def _web_checks(base_url: str, user: str, project_name: str) -> list[Check]:
    """Проверяет, что веб-панель видит результаты, сделанные консолью Host ⇄ Pi."""
    import httpx

    checks: list[Check] = []
    with httpx.Client(base_url=base_url, timeout=30, cookies={"nick": user}) as client:
        try:
            ping = client.get("/api/cryless/ping")
            checks.append(Check("Веб-панель отвечает (API)",
                                ping.status_code == 200,
                                f"HTTP {ping.status_code}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("Веб-панель отвечает (API)", False, str(exc)))
            return checks

        try:
            dash = client.get("/dashboard")
            seen = dash.status_code == 200 and project_name in dash.text
            checks.append(Check("Проект виден в панели (единая БД)", seen,
                                f"HTTP {dash.status_code}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("Проект виден в панели (единая БД)", False, str(exc)))

        try:
            journal = client.get("/journal")
            has_row = journal.status_code == 200 and project_name in journal.text
            checks.append(Check("Работа консоли видна в журнале панели", has_row,
                                f"HTTP {journal.status_code}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("Работа консоли видна в журнале панели", False, str(exc)))
    return checks


# ------------------------------------------------------------------ сам тест

def run_test_mode(
    port: int = core.DEFAULT_HOST_PORT,
    timeout: float = 60.0,
    notify: Callable[[str], None] = print,
    demo_dir: Optional[str] = None,
    storage_dir: Optional[str] = None,
    isolated: bool = False,
    with_web: bool = False,
    web_port: int = 8000,
) -> int:
    """Совмещает Host и Pi в одном процессе и проверяет всю цепочку.

    Возвращает 0, если все проверки пройдены, иначе 1.
    """
    workdir = Path(tempfile.mkdtemp(prefix="gs_test_mode_"))

    # --- изоляция (по флагу --test-isolated): временные хранилище и БД -------
    if isolated:
        storage = workdir / "storage"
        db_file = storage / "team_server.db"
        storage_dir = str(storage)
        os.environ["TEAM_SERVER_DB"] = str(db_file)
        # app_platform.db читает путь при импорте — если модуль уже загружен
        # (in-process запуск), поправляем его напрямую.
        try:
            from app_platform import db as pdb

            pdb.DB_PATH = db_file
        except Exception:  # noqa: BLE001
            pass

    if storage_dir:
        resolved_storage = str(Path(storage_dir).expanduser().resolve())
    else:
        resolved_storage = str(proto.host_storage_dir(port))
    # Хост и веб-панель должны смотреть в одно хранилище, иначе панель не
    # увидит то, что прислал Pi.
    os.environ["GETTINGSTARTED_STORAGE"] = resolved_storage
    os.environ["TEAM_SERVER_DATA"] = resolved_storage

    os.environ["GETTINGSTARTED_HOST_IP"] = "127.0.0.1"

    host_lines: list[str] = []
    pi_lines: list[str] = []

    def host_notify(msg: str) -> None:
        host_lines.append(str(msg))
        notify(f"[Host] {msg}")

    def pi_notify(msg: str) -> None:
        pi_lines.append(str(msg))
        notify(f"[Pi]   {msg}")

    # --- образец проекта (или папка пользователя) ---------------------------
    if demo_dir:
        project = Path(demo_dir).expanduser().resolve()
        if not project.is_dir():
            notify(f"Папка для демо не найдена: {project}")
            shutil.rmtree(workdir, ignore_errors=True)
            return 1
    else:
        project = build_sample_project(workdir / SAMPLE_PROJECT_NAME)
    project_name = project.name

    notify(f"Тестовый режим: Host + Pi в одном процессе (порт {port}).")
    notify(f"Проект для проверки: {project}")
    notify(f"Хранилище: {resolved_storage}")
    if isolated:
        notify("Режим изоляции: временные хранилище и БД (рабочие данные не трогаются).")
    notify("")
    notify("--- Живой журнал обеих сторон (Host ⇄ Pi) ---")

    # --- Host: в фоновом потоке --------------------------------------------
    host_rc: list[int] = []
    host_err: list[BaseException] = []

    def host_job() -> None:
        try:
            host_rc.append(core.run_host_direct(port, timeout, host_notify))
        except BaseException as exc:  # noqa: BLE001 — поток не должен тихо умереть
            host_err.append(exc)

    host_thread = threading.Thread(target=host_job, name="gs-host", daemon=True)
    host_thread.start()
    time.sleep(0.4)  # даём хосту загрузить модули и открыть порт

    # --- Pi: полный рабочий цикл в этом же процессе -------------------------
    conn = core.wait_for_peer("pi", port, timeout, pi_notify)
    if conn is None:
        host_thread.join(timeout=5)
        notify("")
        notify("Тестовый режим: Pi не смог подключиться к Host.")
        checks = [Check("Соединение Host ⇄ Pi", False, "Pi не подключился к хосту")]
        if host_err:
            checks.append(Check("Поток Host без ошибок", False, str(host_err[0])))
        rc = _print_report(checks, notify)
        shutil.rmtree(workdir, ignore_errors=True)
        return rc

    downloaded = workdir / "downloaded" / f"{project_name}.zip"
    png_out = workdir / "diagram.png"
    script = [
        f"up-file {project / 'README.md'}",
        f"up-dir {project}",
        "list",
        f"analyz {project_name}.zip",
        f"tree {project_name}.zip",
        f"png {project_name}.zip {png_out}",
        f"dl projects {project_name}.zip {downloaded}",
        "note тестовый режим: полный цикл",
        "hist",
        "quit",
    ]
    tee: Optional[_Tee] = None
    try:
        tee = _Tee(sys.stdout)
        sys.stdout = tee
        try:
            core.run_pi_client(conn, pi_notify, input, script=script)
        finally:
            sys.stdout = tee._real
    except Exception as exc:  # noqa: BLE001
        pi_notify(f"Ошибка клиента Pi: {exc}")
    host_thread.join(timeout=30)
    pi_stdout = "".join(tee.lines) if tee is not None else ""

    # --- проверки по реальным следам на Host -------------------------------
    storage_root = Path(os.environ["GETTINGSTARTED_STORAGE"])
    checks: list[Check] = []
    host_text = "\n".join(host_lines)
    pi_text = "\n".join(pi_lines)
    pi_full = pi_text + "\n" + pi_stdout

    checks.append(Check(
        "Host запущен (сервер слушает порт)",
        any(line.startswith("Порт:") for line in host_lines),
        f"порт {port}",
    ))
    checks.append(Check(
        "Пара Host ⇄ Pi найдена, сессия установлена",
        "Сессия установлена" in host_text and "Пользователь Host найден" in pi_text,
    ))
    checks.append(Check(
        "Файл Pi → Host",
        (storage_root / "files" / "README.md").is_file()
        and "Pi прислал файл 'README.md'" in host_text
        and "Отправлено на хост" in pi_text,
    ))
    checks.append(Check(
        "Проект Pi → Host",
        (storage_root / "projects" / project_name).is_dir()
        and f"Pi прислал проект '{project_name}'" in host_text,
    ))
    checks.append(Check(
        "Диаграмма кода проекта (gitdiagram-static)",
        "Pi запросил диаграмму кода проекта" in host_text
        and "fallback на дерево" not in host_text
        and "flowchart" in pi_full,
    ))
    checks.append(Check(
        "Mermaid-дерево проекта (app_platform.diagram_gen)",
        "Pi запросил древовидную диаграмму" in host_text
        and pi_full.count("flowchart") >= 2,
    ))
    checks.append(Check(
        "PNG-диаграмма (app_platform.image_gen)",
        png_out.is_file() and png_out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
        f"{png_out.stat().st_size} байт" if png_out.is_file() else "файл не создан",
    ))
    downloaded_ok = downloaded.is_file()
    if downloaded_ok:
        try:
            with zipfile.ZipFile(downloaded) as zf:
                downloaded_ok = any(n.endswith("main.py") for n in zf.namelist())
        except zipfile.BadZipFile:
            downloaded_ok = False
    checks.append(Check(
        "Скачивание Host → Pi (zip проекта)",
        downloaded_ok and "Pi скачал" in host_text and "Сохранено:" in pi_text,
    ))

    # Журнал: читаем ту же БД, что наполняет хост (app_platform.db)
    journal_ok, journal_detail = _journal_state(project_name)
    checks.append(Check(
        "Журнал работы (app_platform.db)",
        journal_ok and "Моя история:" in pi_text,
        journal_detail,
    ))

    host_ok = bool(host_rc) and host_rc[0] == 0
    checks.append(Check(
        "Корректное завершение",
        host_ok and "Сессия завершена" in host_text,
        "; ".join(str(e) for e in host_err) if host_err else "",
    ))

    # --- необязательная проверка веб-панели ---------------------------------
    if with_web:
        base_url, stop = _start_web(web_port, notify)
        if base_url is None:
            checks.append(Check("Веб-панель (--with-web)", False, "не удалось запустить"))
        else:
            checks.extend(_web_checks(base_url, "pi", project_name))
            if stop:
                stop()

    rc = _print_report(checks, notify)
    shutil.rmtree(workdir, ignore_errors=True)
    return rc


def _journal_state(project_name: str) -> tuple[bool, str]:
    """Проверяет единую БД: есть действия и зарегистрированный проект."""
    try:
        from app_platform import db as pdb
    except Exception as exc:  # noqa: BLE001
        return False, f"БД недоступна: {exc}"
    try:
        actions = pdb.list_actions(limit=200)
        projects = pdb.list_projects()
    except Exception as exc:  # noqa: BLE001
        return False, f"ошибка чтения БД: {exc}"

    wanted = {"upload", "analyze", "tree_diagram", "render_png", "download", "note"}
    seen = {str(a.get("action")) for a in actions}
    has_actions = bool(seen & wanted)
    has_project = any(p.get("name") == project_name for p in projects)
    detail = f"{len(actions)} записей, проект {'есть' if has_project else 'нет'}"
    return has_actions and has_project, detail


if __name__ == "__main__":
    raise SystemExit(run_test_mode())
