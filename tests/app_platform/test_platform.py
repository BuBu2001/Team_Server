"""Тесты веб-панели Team Server (app_platform).

Проверяют именно СВЯЗНОСТЬ модулей, а не отдельные функции:
  * загрузка проекта (zip) и анализ через gitingest;
  * диаграмма кода gitdiagram-static + fallback на диаграмму по дереву;
  * PNG-диаграмма (app_platform.image_gen) сохраняется на диск;
  * журнал работы (app_platform.db) пишется и показывается на /journal;
  * проект из консоли Host ⇄ Pi (source = .zip-файл) корректно анализируется;
  * права: Pi не скачивает чужие данные, Host — может.

Тесты изолированы: своя временная БД (TEAM_SERVER_DB/DB_PATH) и свои
каталоги data/, поэтому рабочая data/team_server.db не затрагивается.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOP_FIXTURE = REPO_ROOT / "gitdiagram-static" / "tests" / "fixtures" / "shop_api"


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    """Изолированный экземпляр веб-панели с временной БД и хранилищами."""
    monkeypatch.setenv("GITINGEST_METRICS_ENABLED", "false")

    from app_platform import db
    from app_platform import main as platform_main

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "team_server.db")
    monkeypatch.setattr(platform_main, "UPLOAD_DIR", tmp_path / "projects")
    monkeypatch.setattr(platform_main, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(platform_main, "PI_FILES_DIR", tmp_path / "files")

    with TestClient(platform_main.app) as client:
        yield client, platform_main, db, tmp_path


def _login(client: TestClient, nick: str, role: str) -> None:
    client.post("/register", data={"nick": nick}, follow_redirects=False)
    client.post("/choose-role", data={"role": role}, follow_redirects=False)
    # ники не сбрасываются между запросами: кука у TestClient одна — для двух
    # пользователей используем два клиента (см. тест прав).


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_upload_and_analyze_zip(platform):
    client, _main, db, _tmp = platform
    _login(client, "vanya_pi", "pi")

    resp = client.post(
        "/upload",
        data={"name": "demo", "source": ""},
        files={"archive": ("demo.zip", _zip_bytes({"package/mod.py": "X=1\n"}), "application/zip")},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text

    project = db.list_projects()[0]
    assert Path(project["source"]).is_dir()  # zip распакован в папку проекта

    resp = client.post(f"/project/{project['id']}/analyze", follow_redirects=False)
    assert resp.status_code == 200, resp.text
    assert "Диаграмма" in resp.text

    # PNG (image_gen) и mermaid-диаграмма сохранены на диск
    exports = _main.EXPORT_DIR
    assert (exports / f"project_{project['id']}_tree.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    diagram = (exports / f"project_{project['id']}_diagram.mmd").read_text()
    assert "flowchart" in diagram and "-->" in diagram  # fallback на дерево даёт узлы

    # страница диаграммы отдаёт mermaid
    resp = client.get(f"/project/{project['id']}/diagram")
    assert resp.status_code == 200 and "mermaid" in resp.text


def test_journal_records_actions(platform):
    client, _main, db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    client.post("/upload", data={"name": "x", "source": ""},
                files={"archive": ("x.zip", _zip_bytes({"a.py": "1\n"}), "application/zip")},
                follow_redirects=False)

    rows = db.list_actions()
    assert any(r["action"] == "upload" for r in rows)

    resp = client.get("/journal")
    assert resp.status_code == 200 and "upload" in resp.text

    # действия видны и в панели
    resp = client.get("/dashboard")
    assert resp.status_code == 200 and "Журнал работы" in resp.text


def test_console_project_with_zip_source_is_analyzable(platform):
    """Проект, присланный из консоли Host ⇄ Pi: source указывает на .zip-файл."""
    client, _main, db, tmp = platform
    _login(client, "vanya_pi", "pi")

    zip_path = tmp / "projects" / "src.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(_zip_bytes({"app.py": "print('hi')\n"}))
    project = db.register_project("pi", "src", str(zip_path))

    resp = client.post(f"/project/{project['id']}/analyze", follow_redirects=False)
    assert resp.status_code == 200, resp.text
    assert "Диаграмма" in resp.text


def test_single_file_source_returns_clear_error(platform):
    """Одиночный файл — не проект: панель отвечает понятной ошибкой, а не 500."""
    client, _main, db, tmp = platform
    _login(client, "vanya_pi", "pi")

    note = tmp / "files" / "note.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("привет")
    project = db.register_project("pi", "note", str(note))

    resp = client.post(f"/project/{project['id']}/analyze", follow_redirects=False)
    assert resp.status_code == 422
    assert "одиночный файл" in resp.text


def test_code_diagram_from_gitdiagram_static(platform):
    """Диаграмма кода: gitdiagram-static даёт узлы для многомодульного проекта."""
    client, _main, db, _tmp = platform
    _login(client, "vanya_pi", "pi")

    project = db.register_project("pi", "shop_api", str(SHOP_FIXTURE))
    resp = client.post(f"/project/{project['id']}/analyze", follow_redirects=False)
    assert resp.status_code == 200, resp.text
    assert "архитектуры кода" in resp.text


def test_permissions_pi_cannot_download_others(platform):
    """Pi не получает чужие данные; Host — получает (роль теперь что-то значит)."""
    client, platform_main, db, tmp = platform
    _login(client, "vanya_pi", "pi")

    other = db.register_project("host_one", "secret", str(tmp))
    digest = platform_main.EXPORT_DIR / f"project_{other['id']}_digest.txt"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("secret")

    # Pi: чужие данные недоступны
    resp = client.get(f"/download/digest/{other['id']}")
    assert resp.status_code == 403

    # Host: те же данные доступны
    with TestClient(platform_main.app) as host:
        host.post("/register", data={"nick": "host_one"}, follow_redirects=False)
        host.post("/choose-role", data={"role": "host"}, follow_redirects=False)
        resp = host.get(f"/download/digest/{other['id']}")
        assert resp.status_code == 200 and resp.text == "secret"
