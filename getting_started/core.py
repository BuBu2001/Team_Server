"""Общая логика консоли GettingStarted (используется Windows- и Linux-версии).

Сценарий:
  1. Два пользователя запускают консоль GettingStarted.
  2. Приветствие: "Приветствуем! Выберите режимы работы".
  3. Host — тот, кто будет хостом (хост выполняет все функции, нужные для работы).
     Pi   — обычный пользователь.
  4. Host видит "Ждите пользователя Pi", Pi видит "Ждите пользователя Host".
  5. Когда оба дождались друг друга — сессия установлена; хост выполняет
     рабочие функции (генерация диаграмм через gitdiagram-static) и отдаёт
     результат клиенту Pi.

Транспорт — простой TCP-протокол length-prefixed JSON (без внешних
зависимостей). Хост слушает DEFAULT_HOST_PORT, клиент подключается по IP хоста.
"""

from __future__ import annotations

import json
import os
import select
import socket
import sys
import time
from typing import Any, Callable, Optional

APP_NAME = "GettingStarted"
DEFAULT_HOST_PORT = 47823
LINE = "=" * 62

MODES = {
    "1": ("host", "Host - Это для тех, кто будет хостом. "
                   "Хост выполняет все функции, которые нужны для работы."),
    "2": ("pi",   "Pi   - это обычный пользователь."),
}


# ------------------------------------------------------------------ ввод/вывод

def clear_screen() -> None:
    """Очистка экрана; кроссплатформенно вызывается из get_os_specific()."""
    import os

    if os.name == "nt":
        os.system("cls")
    else:
        os.system("clear")


def print_banner() -> None:
    print(LINE)
    print(f"  {APP_NAME} — консоль подключения к хосту")
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


# ------------------------------------------------------------- сетевой протокол

def send_msg(sock: socket.socket, obj: dict[str, Any]) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sock.sendall(len(data).to_bytes(4, "big") + data)


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_msg(sock: socket.socket) -> Optional[dict[str, Any]]:
    header = _recv_exact(sock, 4)
    if header is None:
        return None
    (length,) = struct_unpack(header)
    body = _recv_exact(sock, length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def struct_unpack(header: bytes) -> tuple[int]:
    import struct

    return struct.unpack("!I", header)


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


# ------------------------------------------------------------ роли: host и pi

def run_host_session(
    conn: socket.socket,
    on_pair: Callable[[socket.socket], None],
    notify: Callable[[str], None],
) -> None:
    """Хост: ждёт пользователя Pi, затем выполняет рабочие функции."""
    hello = recv_msg(conn)
    if not hello or hello.get("role") != "pi":
        notify("Подключился не Pi — соединение закрыто.")
        conn.close()
        return
    send_msg(conn, {"type": "hello-ack", "role": "host"})
    # Оба дождались друг друга — сессия установлена.
    notify("Пользователь Pi подключён. Сессия установлена.")
    on_pair(conn)


def wait_for_peer(
    mode: str,
    port: int,
    timeout: float,
    notify: Callable[[str], None],
    announce_host: bool = True,
) -> Optional[socket.socket]:
    """Блокирующий «Ждите пользователя …» для обеих ролей."""
    deadline = time.time() + timeout if timeout > 0 else None
    if mode == "host":
        notify("Ждите пользователя Pi...")
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(1)
        # Ждём либо подключения Pi, либо нажатия Enter (отмена).
        # Отмена по Enter работает только в интерактивной консоли (tty):
        # при неинтерактивном запуске stdin может вернуть EOF мгновенно,
        # и select всегда считает его «читаемым» — игнорируем такой stdin,
        # чтобы хост не завершался раньше, чем Pi успеет подключиться.
        cancel_fd = sys.stdin.fileno() if sys.stdin.isatty() else None
        conn = addr = None
        while conn is None:
            fds = [srv] + ([cancel_fd] if cancel_fd is not None else [])
            try:
                readable, _, _ = select.select(fds, [], [], 0.5)
            except OSError as exc:
                notify(f"Ошибка сокета: {exc}")
                srv.close()
                return None
            if srv in readable:
                conn, addr = srv.accept()
            elif cancel_fd is not None and cancel_fd in readable:
                line = sys.stdin.readline()
                if line == "":  # EOF: stdin закрыт/перенаправлен — не отменяем ожидание
                    cancel_fd = None
                    continue
                notify("Ожидание отменено пользователем.")
                srv.close()
                return None
            elif timeout > 0 and time.time() >= deadline:
                notify("Таймаут ожидания. Пользователь Pi не подключился.")
                srv.close()
                return None
        srv.close()
        notify(f"Пользователь Pi найден ({addr[0]}:{addr[1]}).")
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
        # Неинтерактивный запуск: ждём хост на localhost.
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
    send_msg(conn, {"type": "hello", "role": "pi"})
    ack = recv_msg(conn)
    if not ack or ack.get("role") != "host":
        notify("Ответ хоста не получен — соединение закрыто.")
        conn.close()
        return None
    notify("Пользователь Host найден.")
    return conn


# --------------------------------------------------------- рабочий процесс пары

def make_host_handler(gitdiagram_main: Callable[[list[str]], int],
                      notify: Callable[[str], None]) -> Callable[[socket.socket], None]:
    """Функции хоста: принимать запросы Pi и запускать gitdiagram-static."""

    def handle(conn: socket.socket) -> None:
        with conn:
            while True:
                req = recv_msg(conn)
                if req is None:
                    notify("Пользователь Pi отключился. Сессия завершена.")
                    return
                if req.get("type") == "quit":
                    send_msg(conn, {"type": "bye"})
                    return
                target = str(req.get("target", "")).strip()
                if not target:
                    send_msg(conn, {"type": "error", "message": "Пустой target."})
                    continue
                notify(f"Запрос от Pi: анализ '{target}'")
                argv = [target, "--stdout"]
                extra = req.get("args") or []
                argv += [str(a) for a in extra]
                try:
                    code = gitdiagram_main(argv)
                    send_msg(conn, {"type": "done", "code": code})
                except SystemExit as exc:  # argparse может выйти сам
                    send_msg(conn, {"type": "done", "code": int(exc.code or 0)})
                except Exception as exc:  # noqa: BLE001
                    send_msg(conn, {"type": "error", "message": str(exc)})

    return handle


def run_pi_client(conn: socket.socket, notify: Callable[[str], None]) -> None:
    """Pi: отправляет хосту путь/репозиторий и получает результат анализа."""
    with conn:
        while True:
            try:
                target = input("Введите репозиторий или путь для анализа "
                               "(пустая строка — выйти): ").strip()
            except EOFError:
                target = ""
            if not target:
                send_msg(conn, {"type": "quit"})
                recv_msg(conn)
                notify("Отключение от хоста. Пока!")
                return
            send_msg(conn, {"type": "analyze", "target": target})
            resp = recv_msg(conn)
            if resp is None:
                notify("Хост потерял соединение.")
                return
            if resp.get("type") == "error":
                notify(f"Ошибка хоста: {resp.get('message')}")
            else:
                notify(f"Хост завершил анализ (код {resp.get('code')}).")
