#!/usr/bin/env python3
"""GettingStarted — консоль для Linux.

Запуск (двойным кликом через файловый менеджер или из терминала):
    ./gettingstarted_linux.py [--port 47823] [--timeout 0]

См. core.py — общая логика Host/Pi и подключения к хосту.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# Интеграция с проектом gitdiagram-static: добавляем его src в sys.path,
# чтобы хост мог выполнять функции анализа напрямую (без pip install).
_HERE = Path(__file__).resolve().parent
_GITDIAGRAM_SRC = _HERE.parent / "gitdiagram-static" / "src"
if _GITDIAGRAM_SRC.is_dir():
    sys.path.insert(0, str(_GITDIAGRAM_SRC))


def get_os_specific() -> dict:
    """Параметры, специфичные для Linux."""
    terminal = None
    if not sys.stdin.isatty() and shutil.which("x-terminal-emulator"):
        # Двойной клик из файлового менеджера — запускаем в терминале.
        terminal = ["x-terminal-emulator", "-e", sys.executable, __file__, *sys.argv[1:]]
    return {
        "name": "Linux",
        "clear": lambda: os.system("clear"),
        "ask": input,
        "terminal_launch": terminal,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="GettingStarted (Linux)")
    parser.add_argument("--port", type=int, default=None, help="порт хоста")
    parser.add_argument("--timeout", type=float, default=0,
                        help="секунды ожидания пары (0 — ждать всегда)")
    # Необязательные флаги для неинтерактивного запуска/тестов:
    # пропускают интерактивное меню и ввод IP хоста.
    parser.add_argument("--mode", choices=["host", "pi", "test"], default=None,
                        help="режим без интерактивного меню: host, pi или test "
                             "(test = Host + Pi в одном процессе, для проверки)")
    parser.add_argument("--host", default=None,
                        help="IP хоста для режима Pi без интерактивного ввода")
    # Флаги автоматизации (для e2e-тестов и скриптов):
    parser.add_argument("--script", default=None,
                        help="список команд Pi через запятую (up-dir <путь>,analyz <имя>,...)")
    parser.add_argument("--auto-demo", action="store_true",
                        help="Pi: сразу выполнить полный демо-цикл и выйти")
    parser.add_argument("--demo-dir", default=None,
                        help="папка проекта для демо-цикла")
    # Флаги тестового режима (--mode test):
    parser.add_argument("--test-storage", default=None,
                        help="тестовый режим: своё хранилище (по умолчанию общее data/)")
    parser.add_argument("--test-isolated", action="store_true",
                        help="тестовый режим: временные хранилище и БД, рабочие данные не трогаются")
    parser.add_argument("--with-web", action="store_true",
                        help="тестовый режим: дополнительно проверить веб-панель")
    parser.add_argument("--web-port", type=int, default=8000,
                        help="тестовый режим: порт веб-панели для --with-web")
    args = parser.parse_args(argv)

    osinfo = get_os_specific()
    if osinfo["terminal_launch"] and argv is None:
        os.execvp(osinfo["terminal_launch"][0], osinfo["terminal_launch"])

    from core import (
        DEFAULT_HOST_PORT,
        choose_mode,
        load_modules,
        local_ip,
        print_banner,
        run_host_direct,
        run_pi_client,
        wait_for_peer,
    )

    port = args.port or DEFAULT_HOST_PORT
    notify = print

    if args.mode == "test":
        # Тестовый режим: Host и Pi совмещены в одном процессе.
        print_banner()
        print("Приветствуем! Тестовый режим: Host и Pi работают вместе "
              "на одной машине.")
        import test_mode  # файл рядом с этим скриптом (см. sys.path[0])

        return test_mode.run_test_mode(
            port=port,
            timeout=args.timeout or 60.0,
            notify=notify,
            demo_dir=args.demo_dir,
            storage_dir=args.test_storage,
            isolated=args.test_isolated,
            with_web=args.with_web,
            web_port=args.web_port,
        )

    if args.mode:
        mode = args.mode
        print_banner()
        print(f"Приветствуем! Режим выбран автоматически: "
              f"{'Host' if mode == 'host' else 'Pi'}")
    else:
        mode = choose_mode(osinfo["ask"])

    if mode == "host":
        notify(f"\nВы выбрали режим Host. Порт: {port}. "
               f"IP для пользователя Pi: {local_ip()}")
        return run_host_direct(port, args.timeout, notify) or 0
    else:
        notify("\nВы выбрали режим Pi (обычный пользователь).")
        if args.host:
            os.environ["GETTINGSTARTED_HOST_IP"] = args.host
        conn = wait_for_peer("pi", port, args.timeout, notify)
        if conn is None:
            return 1
        script = args.script.split(",") if args.script else None
        auto = {"auto_demo": bool(args.auto_demo), "demo_dir": args.demo_dir}
        run_pi_client(conn, notify, osinfo["ask"], script=script, auto=auto)

    print_banner()
    notify("Спасибо за использование GettingStarted!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
