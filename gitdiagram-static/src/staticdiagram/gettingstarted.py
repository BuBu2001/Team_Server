"""Интеграция консоли GettingStarted (Host/Pi) с gitdiagram-static.

GettingStarted — единая локальная система из двух консолей, которые
устанавливаются на два компьютера в одной сети:

  * Host — сервер: принимает файлы/проекты от Pi, строит для них диаграммы
    (gitdiagram-static + app_platform.diagram_gen/image_gen), ведёт журнал
    работы (app_platform.db) и отдаёт результаты по сети;
  * Pi   — обычный пользователь: отправляет файлы хосту, смотрит диаграммы,
    скачивает данные обратно, следит за своей работой.

Модуль предоставляет subcommand для CLI::

    gitdiagram-static connect                 # интерактивный выбор Host / Pi
    gitdiagram-static connect --role host
    gitdiagram-static connect --role pi --host 192.168.1.10
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# ../../ от src/staticdiagram -> корень monorepo, там getting_started/
_ROOT = Path(__file__).resolve().parents[3]
GETTING_STARTED_DIR = _ROOT / "getting_started"


def is_available() -> bool:
    return (GETTING_STARTED_DIR / "core.py").is_file()


def _bootstrap() -> None:
    """Достаёт core.py и пакет staticdiagram (на случай запуска без install)."""
    for p in (str(GETTING_STARTED_DIR), str(_ROOT / "gitdiagram-static" / "src"),
              str(_ROOT)):
        if p not in sys.path and Path(p).is_dir():
            sys.path.insert(0, p)


def run_connect(
    role: Optional[str] = None,
    port: Optional[int] = None,
    timeout: float = 0,
    host_ip: Optional[str] = None,
    script: Optional[list] = None,
    auto_demo: bool = False,
    demo_dir: Optional[str] = None,
) -> int:
    """Запускает консоль GettingStarted. role: None — выбрать в меню,
    'host'/'pi' — пропустить меню. Возвращает код выхода."""
    if not is_available():
        print(f"GettingStarted не найден в {GETTING_STARTED_DIR}", file=sys.stderr)
        return 127
    _bootstrap()
    from core import (  # noqa: E402
        DEFAULT_HOST_PORT,
        choose_mode,
        load_modules,
        print_banner,
        run_host_direct,
        run_pi_client,
        wait_for_peer,
    )

    port = port or DEFAULT_HOST_PORT
    notify = print

    if role is None:
        role = choose_mode(input)
    elif role not in ("host", "pi"):
        print("Неверная роль. Используйте --role host или --role pi.", file=sys.stderr)
        return 2

    if role == "host":
        notify(f"\nВы выбрали режим Host. Порт: {port}.")
        return run_host_direct(port, timeout, notify) or 0

    notify("\nВы выбрали режим Pi (обычный пользователь).")
    if host_ip:
        import os

        os.environ["GETTINGSTARTED_HOST_IP"] = host_ip
    conn = wait_for_peer("pi", port, timeout, notify)
    if conn is None:
        return 1
    run_pi_client(conn, notify, input, script=script,
                  auto={"auto_demo": auto_demo, "demo_dir": demo_dir})
    return 0


def add_connect_subcommand(subparsers) -> None:
    """Регистрирует `connect` в argparse-парсере staticdiagram."""
    parser = subparsers.add_parser(
        "connect",
        help="запустить консоль GettingStarted (Host/Pi: файлы, диаграммы, журнал)",
    )
    parser.add_argument("--role", choices=["host", "pi"], default=None,
                        help="режим; по умолчанию — выбор в меню")
    parser.add_argument("--port", type=int, default=None,
                        help="порт хоста (default: 47823)")
    parser.add_argument("--timeout", type=float, default=0,
                        help="секунды ожидания пары (0 — ждать всегда)")
    parser.add_argument("--host", dest="host_ip", default=None,
                        help="IP хоста (для режима Pi)")
    parser.add_argument("--script", nargs="*", default=None,
                        help="команды Pi без интерактива (тесты): up-dir <путь>, ...")
    parser.add_argument("--auto-demo", action="store_true",
                        help="Pi: выполнить полный демо-цикл и выйти")
    parser.add_argument("--demo-dir", default=None,
                        help="папка проекта для демо-цикла")


def handle_connect_args(args) -> Optional[int]:
    """Если команда — `connect`, выполняет её и возвращает код выхода."""
    cmd = getattr(args, "command", None)
    if cmd != "connect":
        return None
    return run_connect(role=args.role, port=args.port,
                       timeout=args.timeout, host_ip=args.host_ip,
                       script=getattr(args, "script", None),
                       auto_demo=getattr(args, "auto_demo", False),
                       demo_dir=getattr(args, "demo_dir", None))
