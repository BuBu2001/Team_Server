#!/usr/bin/env python3
"""E2E-тест единой системы GettingStarted: Host ⇄ Pi по локальному TCP.

Проверяет, что ВСЕ модули включены в систему и работают вместе:
  1. приветствие и выбор режимов (Host/Pi), взаимное ожидание пары;
  2. передача файлов: Pi -> Host (upload файла и проекта-папки);
  3. диаграммы на хосте: gitdiagram-static (analyz) + app_platform.diagram_gen
     (tree) + app_platform.image_gen (png);
  4. скачивание обратно: Host -> Pi (download);
  5. журнал работы: app_platform.db (log_action / history);
  6. интеграция через CLI gitdiagram-static (`connect`).

Запуск:  python3 getting_started/tests/e2e_system.py
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GS_DIR = HERE.parent
REPO = GS_DIR.parent
PORT = 47931


def wait_port(port: int, want: bool, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.3)
            try:
                # connect() к слушающему сокету не является handshake и не
                # отправляет байты — хост увидит пустое соединение и корректно
                # его закроет (см. handle(hello=None) в core.py).
                s.connect(("127.0.0.1", port))
                up = True
            except OSError:
                up = False
        if up == want:
            return
        time.sleep(0.2)
    raise RuntimeError(f"порт {port}: ожидался up={want}")


def main() -> int:
    demo = Path(tempfile.mkdtemp(prefix="gs_demo_"))

    src = demo / "src"
    src.mkdir()
    (src / "app.py").write_text("def hello():\n    return 'hi'\n")
    (demo / "README.md").write_text("# Демо-проект\n")
    note = demo / "note.txt"
    note.write_text("привет от Pi")

    env = dict(os.environ)
    env["GETTINGSTARTED_STORAGE"] = str(demo / "storage")
    # Ключевая фикстура: временная БД журнала. Иначе app_platform.db указывает на
    # общий data/team_server.db, SQLite-блокировки которого при конкурентном
    # доступе (несколько тестов/сессий) вызывают зависание хоста на journal().
    env["TEAM_SERVER_DB"] = str(demo / "storage" / "journal_test.db")
    env["PYTHONPATH"] = f"{REPO / 'gitdiagram-static' / 'src'}{os.pathsep}{REPO}"
    out_dir = demo / "out"
    out_dir.mkdir()

    # 1) Хост — запускаем ЧЕРЕЗ CLI gitdiagram-static (проверка интеграции).
    host_log = open(out_dir / "host.log", "wb")
    host = subprocess.Popen(
        [sys.executable, "-m", "staticdiagram", "connect",
         "--role", "host", "--port", str(PORT)],
        cwd=str(REPO / "gitdiagram-static"), stdin=subprocess.DEVNULL,
        stdout=host_log, stderr=subprocess.STDOUT, env=env)
    time.sleep(3.0)  # даём хосту подняться; НЕ трогаем порт — слушатель
                     # принимает ровно одно соединение (listen(1)), пробная
                     # connect'ом проверка «съедала» слот и Pi не мог подключиться.
    try:
        # 2) Pi — полный рабочий цикл неинтерактивно.
        back = str(out_dir / "project_back.zip")
        png_out = str(out_dir / "diagram.png")
        script = ",".join([
            f"up-file {note}",
            f"up-dir {src}",
            "list",
            "analyz src.zip",
            "tree src.zip",
            f"png src.zip {png_out}",
            f"dl projects src.zip {back}",
            "note сделал демо-цикл",
            "hist",
            "quit",
        ])
        pi_log = open(out_dir / "pi.log", "wb")
        try:
            pi = subprocess.run(
                [sys.executable, str(GS_DIR / "gettingstarted_linux.py"),
                 "--mode", "pi", "--port", str(PORT), "--host", "127.0.0.1",
                 "--script", script],
                stdin=subprocess.DEVNULL, stdout=pi_log,
                stderr=subprocess.STDOUT, env=env, timeout=120)
        finally:
            pi_log.close()
        pi_rc = pi.returncode
        host.wait(timeout=30)
    finally:
        if host.poll() is None:
            host.kill()
            host.wait()
        host_log.close()

    host_out = (out_dir / "host.log").read_text(errors="replace")
    pi_out = (out_dir / "pi.log").read_text(errors="replace")
    print(host_out)
    print(pi_out)

    # Единая БД системы: проект, присланный из консоли, должен попасть в журнал
    # и быть виден веб-панели. Читаем именно изолированную БД теста
    # (TEAM_SERVER_DB), чтобы проверка не зависела от рабочей data/team_server.db.
    db_names: list[str] | None = None
    db_file = Path(env["TEAM_SERVER_DB"])
    if db_file.is_file():
        import sqlite3

        with sqlite3.connect(db_file) as conn:
            db_names = [r[0] for r in
                        conn.execute("SELECT name FROM projects").fetchall()]

    checks = [
        ("Приветствие/режимы (host)", "Режим выбран автоматически: Host" in host_out or
                                      "Вы выбрали режим Host" in host_out),
        ("Ожидание Pi (host)",        "Ждите пользователя Pi" in host_out),
        ("Ожидание Host (pi)",        "Ждите пользователя Host" in pi_out),
        ("Пара найдена (host)",       "Пользователь Pi найден" in host_out and
                                      "Сессия установлена" in host_out),
        ("Пара найдена (pi)",         "Пользователь Host найден" in pi_out),
        ("Upload файла на Host",      "Pi прислал файл 'note.txt'" in host_out and
                                      "Отправлено на хост" in pi_out),
        ("Upload проекта на Host",    "Pi прислал проект 'src'" in host_out),
        ("Хранилище хоста (общее с веб-панелью)",
                                      (Path(env["GETTINGSTARTED_STORAGE"]) /
                                       "projects" / "src").is_dir() and
                                      (Path(env["GETTINGSTARTED_STORAGE"]) /
                                       "files" / "note.txt").is_file()),
        ("Проект виден веб-панели (единая БД)",
                                      db_names is not None and "src" in db_names),
        ("Диаграмма кода (gitdiagram-static или fallback-tree)",
                                      "--- Диаграмма" in pi_out and
                                      "конец диаграммы" in pi_out and
                                      ("flowchart" in pi_out or "graph TD" in pi_out)),
        ("Mermaid-дерево (app_platform.diagram_gen)",
                                      pi_out.count("--- Диаграмма") >= 2),
        ("PNG (app_platform.image_gen)",
                                      Path(png_out).is_file() and
                                      Path(png_out).read_bytes()[:8] ==
                                      b"\x89PNG\r\n\x1a\n"),
        ("Download с Host обратно",   Path(back).is_file() and
                                      "Сохранено:" in pi_out),
        ("Журнал (app_platform.db)",  "upload" in pi_out and "analyze" in pi_out and
                                      "Моя история:" in pi_out),
        ("Корректное завершение",     pi_rc == 0 and
                                      "Сессия завершена" in host_out),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"[{'OK' if ok else 'FAIL'}] {name}")
    shutil.rmtree(demo, ignore_errors=True)
    if failed:
        print(f"E2E FAIL: {failed}")
        return 1
    print("E2E PASS: все модули системы работают вместе (файлы, диаграммы, журнал)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
