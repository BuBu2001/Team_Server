"""Тесты библиотеки команд cryless.

Две группы:

* **юнит-тесты** — нормализация имён (пробелы/кириллица/«+»), сборка архива с
  соблюдением ``.crylessignore``;
* **интеграционные** — настоящий сервер Team Server (uvicorn в подпроцессе) и
  настоящие вызовы CLI (``python -m cryless``): create repository → load data →
  проверка, что данные легли в общее хранилище, проект виден веб-панели,
  диаграмма построена, а журнал записан.

Изоляция: сервер запускается с TEMP-каталогами (TEAM_SERVER_DATA/TEAM_SERVER_DB),
поэтому рабочая data/ и рабочая БД не затрагиваются. Настройки CLI (CRYLESS_HOME)
тоже указывают в TEMP.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CRYLESS_SRC = REPO_ROOT / "cryless" / "src"


# ---------- служебное ----------

def run_cli(args: list[str], cwd: Path, env: dict) -> subprocess.CompletedProcess:
    """Запускает настоящий CLI: python -m cryless <args>."""
    pythonpath = os.pathsep.join(
        [str(CRYLESS_SRC), str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    full_env = {**env, "PYTHONPATH": pythonpath}
    return subprocess.run(
        [sys.executable, "-m", "cryless", *args],
        cwd=str(cwd), env=full_env, capture_output=True, text=True, timeout=300,
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_project(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Настоящий сервер Team Server на свободном порту, с TEMP-хранилищами."""
    base = tmp_path_factory.mktemp("cryless_server")
    data_dir = base / "data"
    env = {
        **os.environ,
        "TEAM_SERVER_DB": str(base / "team_server.db"),
        "TEAM_SERVER_DATA": str(data_dir),
        "GITINGEST_METRICS_ENABLED": "false",
        "PYTHONPATH": str(REPO_ROOT),
    }
    port = free_port()
    log = open(base / "server.log", "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app_platform.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    ready = False
    while time.time() < deadline and proc.poll() is None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.2)
    if not ready:
        proc.kill()
        log.close()
        raise RuntimeError(f"сервер не поднялся; лог: {(base / 'server.log').read_text(errors='replace')[-2000:]}")

    yield SimpleNamespace(
        url=url,
        env=env,
        base=base,
        data_dir=data_dir,
        db_path=base / "team_server.db",
        projects_dir=data_dir / "projects",
        exports_dir=data_dir / "exports",
    )

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


@pytest.fixture()
def cli_env(tmp_path, server):
    """Окружение CLI: свои настройки и ник, сервер — из фикстуры."""
    home = tmp_path / "cryless_home"
    home.mkdir()
    return {
        **server.env,
        "CRYLESS_HOME": str(home),
        "CRYLESS_SERVER": server.url,
        "CRYLESS_USER": "vanya_pi",
    }


def db_rows(server, query: str, args: tuple = ()) -> list[tuple]:
    with sqlite3.connect(server.db_path) as conn:
        return conn.execute(query, args).fetchall()


# ---------- юнит: правила имён ----------

def test_validate_repo_name_rejects_spaces_and_cyrillic():
    from cryless.naming import validate_repo_name
    from cryless.errors import CrylessError

    for bad in ["мой проект", "проект", "my project", "my\tproject", "имя_проекта"]:
        with pytest.raises(CrylessError) as e:
            validate_repo_name(bad)
        text = str(e.value)
        assert "пробел" in text or "кириллица" in text

    for good in ["my_project", "api-v2", "site.ru", "App2"]:
        assert validate_repo_name(good) == good


def test_validate_repo_name_rejects_dots_and_length():
    from cryless.naming import validate_repo_name
    from cryless.errors import CrylessError

    with pytest.raises(CrylessError):
        validate_repo_name(".")
    with pytest.raises(CrylessError):
        validate_repo_name("name.")
    with pytest.raises(CrylessError):
        validate_repo_name("a..b")
    with pytest.raises(CrylessError):
        validate_repo_name("x" * 100)


def test_validate_username():
    from cryless.naming import validate_username
    from cryless.errors import CrylessError

    assert validate_username("vanya_pi") == "vanya_pi"
    with pytest.raises(CrylessError):
        validate_username("ваня")
    with pytest.raises(CrylessError):
        validate_username("v")


# ---------- юнит: сборка данных ----------

def test_pack_repo_respects_crylessignore(tmp_path):
    from cryless.localrepo import pack_repo, cleanup_pack

    project = make_project(tmp_path / "proj", {
        "README.md": "# hi\n",
        "src/app.py": "print(1)\n",
        ".env": "SECRET=1\n",
        "debug.log": "log\n",
        "__pycache__/x.pyc": "junk\n",
        ".venv/lib/site.py": "junk\n",
    })
    (project / ".cryless").mkdir()
    (project / ".cryless" / "config.json").write_text('{"token": "SECRET-TOKEN"}')
    result = pack_repo(project)
    try:
        with zipfile.ZipFile(result.archive) as zf:
            names = set(zf.namelist())
        assert {"README.md", "src/app.py"} <= names
        # секреты и мусор не уходят на сервер
        assert not any(n.startswith(".cryless") for n in names)
        assert ".env" not in names and "debug.log" not in names
        assert not any(n.startswith("__pycache__") for n in names)
        assert not any(n.startswith(".venv") for n in names)
        # по умолчанию конфиг .crylessignore берётся встроенный
        assert result.files == 2
        assert any(reason for _p, reason in result.skipped)
    finally:
        cleanup_pack(result)


def test_pack_repo_skips_large_files(tmp_path, monkeypatch):
    from cryless import localrepo

    monkeypatch.setattr(localrepo, "MAX_FILE_BYTES", 16)
    project = make_project(tmp_path / "proj", {
        "small.txt": "ok\n",
        "big.txt": "x" * 100,
    })
    result = localrepo.pack_repo(project)
    try:
        with zipfile.ZipFile(result.archive) as zf:
            names = set(zf.namelist())
        assert names == {"small.txt"}
        assert any(name == "big.txt" for name, _reason in result.skipped)
    finally:
        localrepo.cleanup_pack(result)


# ---------- интеграция: CLI + сервер ----------

def test_connection_check_ok(cli_env, tmp_path):
    proc = run_cli(["connection", "check"], tmp_path, cli_env)
    assert proc.returncode == 0, proc.stderr
    assert "Соединение есть" in proc.stdout
    assert "team-server" in proc.stdout


def test_connection_check_without_server(tmp_path, server):
    """Сервер не слушает порт — понятная ошибка вместо трассировки."""
    env = {**server.env, "CRYLESS_HOME": str(tmp_path / "home")}
    proc = run_cli(["connection", "check", "--server", "http://127.0.0.1:9"], tmp_path, env)
    assert proc.returncode == 1
    assert "Нет соединения с сервером" in proc.stderr


def test_help_and_version(tmp_path, server):
    env = {**server.env, "CRYLESS_HOME": str(tmp_path / "home")}
    help_proc = run_cli(["help"], tmp_path, env)
    assert help_proc.returncode == 0
    for text in ("create repository", "load data", "connection check", "латиницу"):
        assert text in help_proc.stdout

    version_proc = run_cli(["version"], tmp_path, env)
    assert version_proc.returncode == 0 and "cryless" in version_proc.stdout


def test_no_command_is_usage_error(tmp_path, server):
    env = {**server.env, "CRYLESS_HOME": str(tmp_path / "home")}
    proc = run_cli([], tmp_path, env)
    assert proc.returncode == 2
    assert "Не указана команда" in proc.stderr


def test_create_repository_normalizes_bad_names(cli_env, tmp_path, server):
    """Плохие имена больше не ошибка — они приводятся к допустимому виду."""
    from cryless.naming import normalize_repo_name, slugify_repo_name

    assert slugify_repo_name("C++ flag generator") == "cpp-flag-generator"
    assert slugify_repo_name("my project") == "my-project"
    assert slugify_repo_name("мой_проект") == "moy_proekt"
    assert normalize_repo_name("my_project") == ("my_project", None)
    assert normalize_repo_name("my project") == ("my-project", "my project")

    project = make_project(tmp_path / "проект со пробелом", {"a.txt": "1\n"})
    proc = run_cli(["create", "repository", "my project"], project, cli_env)
    assert proc.returncode == 0, proc.stderr
    assert "приведено" in proc.stdout  # предупреждение о переименовании
    config = json.loads((project / ".cryless" / "config.json").read_text())
    assert config["name"] == "my-project"
    assert db_rows(server, "SELECT name FROM projects WHERE name='my-project'")


def test_create_repository_requires_project_root(cli_env, tmp_path):
    empty = tmp_path / "empty_dir"
    empty.mkdir()
    proc = run_cli(["create", "repository", "someproj"], empty, cli_env)
    assert proc.returncode == 1
    assert "не похоже на корень проекта" in proc.stderr


def test_load_data_requires_local_repository(cli_env, tmp_path):
    project = make_project(tmp_path / "norepo", {"a.txt": "1\n"})
    proc = run_cli(["load", "data", "norepo"], project, cli_env)
    assert proc.returncode == 1
    assert "нет репозитория cryless" in proc.stderr


def test_full_flow_create_and_load(cli_env, tmp_path, server):
    """Главный сценарий: create repository → load data → данные и диаграмма на сервере."""
    project = make_project(tmp_path / "my_project", {
        "README.md": "# My project\n",
        "src/app.py": "def hello():\n    return 'hi'\n",
        "src/util.py": "from . import app\n\ndef helper():\n    return app.hello()\n",
        ".env": "SECRET=1\n",
        "debug.log": "log\n",
        "__pycache__/x.pyc": "junk\n",
    })

    # 1) создать репозиторий
    proc = run_cli(["create", "repository", "my_project"], project, cli_env)
    assert proc.returncode == 0, proc.stderr
    assert "создан" in proc.stdout
    config = json.loads((project / ".cryless" / "config.json").read_text())
    assert config["name"] == "my_project" and config["user"] == "vanya_pi" and config["token"]
    assert (project / ".crylessignore").is_file()
    # токен не должен быть доступен другим: файл только для владельца
    assert oct((project / ".cryless" / "config.json").stat().st_mode)[-3:] == "600"

    # проект сразу виден серверу
    assert db_rows(server, "SELECT source FROM projects WHERE name='my_project'")

    # 2) загрузить данные
    proc = run_cli(["load", "data", "my_project"], project, cli_env)
    assert proc.returncode == 0, proc.stderr
    assert "Данные загружены" in proc.stdout
    assert "Диаграмма" in proc.stdout

    # 3) данные лежат в общем хранилище сервера
    storage = server.projects_dir / "vanya_pi__my_project"
    uploaded = {p.relative_to(storage).as_posix() for p in storage.rglob("*") if p.is_file()}
    assert {"README.md", "src/app.py", "src/util.py"} <= uploaded
    assert ".env" not in uploaded and "debug.log" not in uploaded
    # .crylessignore — обычный файл проекта (как .gitignore), а вот .cryless/
    # с токеном доступа на сервер уходить не должен
    assert not any(name.startswith(".cryless/") for name in uploaded)
    assert not any(name.startswith("__pycache__") for name in uploaded)

    # 4) анализ: PNG + mermaid-диаграмма + digest
    project_id = db_rows(server, "SELECT id FROM projects WHERE name='my_project'")[0][0]
    png = server.exports_dir / f"project_{project_id}_tree.png"
    mmd = server.exports_dir / f"project_{project_id}_diagram.mmd"
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "-->" in mmd.read_text()

    # 5) журнал: create и load записаны
    actions = {row[0] for row in db_rows(server, "SELECT action FROM actions")}
    assert {"repo_create", "repo_load"} <= actions

    # 6) репозиторий доступен на основном сайте через JSON-API (единая система)
    api_project = _http_get(server, f"/api/projects/{project_id}", cookies={"nick": "vanya_pi"})
    assert api_project.status_code == 200
    body = api_project.json()
    assert body["project"]["name"] == "my_project" and body["project"]["analyzed"]
    assert body["analysis"]["diagram_code"]

    diagram = _http_get(server, f"/api/projects/{project_id}/diagram", cookies={"nick": "vanya_pi"})
    assert diagram.status_code == 200 and "flowchart" in diagram.json()["code"]

    # сам сайт (SPA) раздаётся по клиентскому маршруту проекта
    page = _http_get(server, f"/projects/{project_id}", cookies={"nick": "vanya_pi"})
    assert page.status_code == 200 and "<div id=\"root\">" in page.text


def test_load_data_replaces_content_like_push(cli_env, tmp_path, server):
    """Повторный load data заменяет содержимое репозитория (как git push)."""
    project = make_project(tmp_path / "push_proj", {
        "keep.txt": "keep\n",
        "gone.txt": "gone\n",
    })
    assert run_cli(["create", "repository", "push_proj"], project, cli_env).returncode == 0
    assert run_cli(["load", "data"], project, cli_env).returncode == 0

    storage = server.projects_dir / "vanya_pi__push_proj"
    assert (storage / "gone.txt").exists()

    (project / "gone.txt").unlink()
    (project / "new.txt").write_text("new\n", encoding="utf-8")
    proc = run_cli(["load", "data", "push_proj"], project, cli_env)
    assert proc.returncode == 0, proc.stderr

    names = {p.name for p in storage.iterdir() if p.is_file()}
    # .crylessignore — файл проекта (как .gitignore), поэтому он на сервере есть
    assert names == {"keep.txt", "new.txt", ".crylessignore"}


def test_force_recreates_and_rotates_token(cli_env, tmp_path, server):
    project = make_project(tmp_path / "rotate", {"a.txt": "1\n"})
    assert run_cli(["create", "repository", "rotate"], project, cli_env).returncode == 0
    old_token = json.loads((project / ".cryless" / "config.json").read_text())["token"]

    # повторное создание без --force — отказ
    again = run_cli(["create", "repository", "rotate"], project, cli_env)
    assert again.returncode == 1 and "уже" in again.stderr

    forced = run_cli(["create", "repository", "rotate", "--force"], project, cli_env)
    assert forced.returncode == 0, forced.stderr
    new_token = json.loads((project / ".cryless" / "config.json").read_text())["token"]
    assert new_token and new_token != old_token

    # старый токен больше не принимается сервером
    resp = _http_post_token(server, "rotate", old_token)
    assert resp.status_code == 401


def test_load_data_in_subdirectory_refuses(cli_env, tmp_path):
    project = make_project(tmp_path / "root_proj", {"src/app.py": "1\n"})
    assert run_cli(["create", "repository", "root_proj"], project, cli_env).returncode == 0
    sub = project / "src"
    proc = run_cli(["load", "data"], sub, cli_env)
    assert proc.returncode == 1
    assert "не в корне проекта" in proc.stderr


# ---------- HTTP-помощники тестов ----------

def _http_get(server, url: str, cookies: dict | None = None) -> httpx.Response:
    with httpx.Client(base_url=server.url, timeout=30, cookies=cookies or {}) as client:
        return client.get(url)


def _http_post_token(server, name: str, token: str) -> httpx.Response:
    with httpx.Client(base_url=server.url, timeout=30) as client:
        return client.post(
            f"/api/cryless/repositories/{name}/data",
            headers={"Authorization": f"Bearer {token}"},
            files={"archive": ("empty.zip", b"", "application/zip")},
        )
