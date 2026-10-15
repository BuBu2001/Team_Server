"""Интеграция консоли GettingStarted (Host/Pi) с gitdiagram-static.

Модуль тонко оборачивает скрипты из ``<корень репозитория>/getting_started``
и предоставляет subcommand для CLI::

    gitdiagram-static connect            # интерактивный выбор Host / Pi
    gitdiagram-static connect --role host
    gitdiagram-static connect --role pi --host 192.168.1.10

Хост выполняет все рабочие функции проекта (генерацию диаграмм через
``staticdiagram.cli.main``), а пользователь Pi подключается к хосту по TCP
и получает результаты анализа.
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
    for p in (str(GETTING_STARTED_DIR), str(_ROOT / "gitdiagram-static" / "src")):
        if p not in sys.path and Path(p).is_dir():
            sys.path.insert(0, p)


def run_connect(
    role: Optional[str] = None,
    port: Optional[int] = None,
    timeout: float = 0,
    host_ip: Optional[str] = None,
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
        make_host_handler,
        run_host_session,
        run_pi_client,
        wait_for_peer,
    )
    from staticdiagram.cli import main as gitdiagram_main  # type: ignore

    port = port or DEFAULT_HOST_PORT
    notify = print

    if role is None:
        role = choose_mode(input)
    elif role not in ("host", "pi"):
        print("Неверная роль. Используйте --role host или --role pi.", file=sys.stderr)
        return 2

    if role == "host":
        notify(f"\nВы выбрали режим Host. Порт: {port}.")
        conn = wait_for_peer("host", port, timeout, notify)
        if conn is None:
            return 1
        run_host_session(conn, make_host_handler(gitdiagram_main, notify), notify)
        return 0

    notify("\nВы выбрали режим Pi (обычный пользователь).")
    if host_ip:
        # Неинтерактивный ввод IP для wait_for_peer.
        _patch_input_default(host_ip)
    conn = wait_for_peer("pi", port, timeout, notify)
    if conn is None:
        return 1
    run_pi_client(conn, notify)
    return 0


def _patch_input_default(value: str) -> None:
    import builtins

    real_input = builtins.input

    def patched(prompt: str = "") -> str:
        if "IP адрес хоста" in prompt:
            print(f"{prompt}{value}")
            return value
        return real_input(prompt)

    builtins.input = patched  # type: ignore[assignment]


def add_connect_subcommand(subparsers) -> None:
    """Регистрирует `connect` в argparse-парсере staticdiagram."""
    parser = subparsers.add_parser(
        "connect",
        help="запустить консоль GettingStarted (Host/Pi подключение к хосту)",
    )
    parser.add_argument("--role", choices=["host", "pi"], default=None,
                        help="режим; по умолчанию — выбор в меню")
    parser.add_argument("--port", type=int, default=None,
                        help="порт хоста (default: 47823)")
    parser.add_argument("--timeout", type=float, default=0,
                        help="секунды ожидания пары (0 — ждать всегда)")
    parser.add_argument("--host", dest="host_ip", default=None,
                        help="IP хоста (для режима Pi)")


def handle_connect_args(args) -> Optional[int]:
    """Если команда — `connect`, выполняет её и возвращает код выхода."""
    cmd = getattr(args, "command", None)
    if cmd != "connect":
        return None
    return run_connect(role=args.role, port=args.port,
                       timeout=args.timeout, host_ip=args.host_ip)
