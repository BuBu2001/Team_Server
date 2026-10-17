"""Тесты основного сайта и JSON-API Team Server (app_platform).

Проверяют именно СВЯЗНОСТЬ модулей, а не отдельные функции:
  * сессия по нику, роли Host/Pi;
  * загрузка проекта (zip) и анализ через gitingest;
  * диаграмма кода gitdiagram-static + fallback на диаграмму по дереву;
  * PNG-диаграмма (app_platform.image_gen) сохраняется на диск;
  * журнал работы (app_platform.db) пишется и отдаётся через /api/journal;
  * проект из консоли Host ⇄ Pi (source = .zip-файл) корректно анализируется;
  * права: Pi не получает чужие материалы, Host — получает;
  * SPA раздаётся по «человеческим» адресам, а неизвестные /api/* — 404.

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
WEB_INDEX = REPO_ROOT / "web" / "dist" / "index.html"


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    """Изолированный экземпляр сайта с временной БД и хранилищами."""
    monkeypatch.setenv("GITINGEST_METRICS_ENABLED", "false")

    from app_platform import db
    from app_platform import main as platform_main

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "team_server.db")
    monkeypatch.setattr(platform_main, "UPLOAD_DIR", tmp_path / "projects")
    monkeypatch.setattr(platform_main, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(platform_main, "PI_FILES_DIR", tmp_path / "files")

    with TestClient(platform_main.app) as client:
        yield client, platform_main, db, tmp_path


def _login(client: TestClient, nick: str, role: str | None = None) -> None:
    resp = client.post("/api/auth/login", json={"nick": nick})
    assert resp.status_code == 200, resp.text
    if role is not None:
        resp = client.post("/api/auth/role", json={"role": role})
        assert resp.status_code == 200, resp.text


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _upload(client: TestClient, name: str, files: dict[str, str]) -> dict:
    resp = client.post(
        "/api/projects",
        data={"name": name, "source": ""},
        files={"archive": ("src.zip", _zip_bytes(files), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["project"]


# ---------- сессия ----------

def test_login_and_role(platform):
    client, _main, _db, _tmp = platform
    assert client.get("/api/projects").status_code == 401  # без сессии

    _login(client, "vanya_pi", "host")
    me = client.get("/api/auth/me").json()["user"]
    assert me["username"] == "vanya_pi" and me["role"] == "host"

    client.post("/api/auth/role", json={"role": "pi"})
    assert client.get("/api/auth/me").json()["user"]["role"] == "pi"

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_login_rejects_bad_nick(platform):
    client, *_ = platform
    resp = client.post("/api/auth/login", json={"nick": "плохой ник"})
    assert resp.status_code == 422


# ---------- проекты ----------

def test_upload_and_analyze_zip(platform):
    client, platform_main, db, _tmp = platform
    _login(client, "vanya_pi", "pi")

    project = _upload(client, "demo", {"package/mod.py": "X=1\n"})
    assert Path(db.get_project(project["id"])["source"]).is_dir()  # zip распакован

    resp = client.post(f"/api/projects/{project['id']}/analyze")
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["summary"] and result["tree"]
    assert result["diagram_code"] and "-->" in result["diagram_code"]

    # PNG (image_gen) и mermaid-диаграмма сохранены на диск
    exports = platform_main.EXPORT_DIR
    assert (exports / f"project_{project['id']}_tree.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "flowchart" in (exports / f"project_{project['id']}_diagram.mmd").read_text()

    # повторная отдача без анализа: проект помечен как проанализированный
    detail = client.get(f"/api/projects/{project['id']}").json()
    assert detail["project"]["analyzed"] is True
    assert detail["analysis"]["diagram_code"]

    diagram = client.get(f"/api/projects/{project['id']}/diagram").json()
    assert "flowchart" in diagram["code"]
    assert diagram["kind"] in ("tree", "code")


def test_journal_and_stats(platform):
    client, _main, _db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    _upload(client, "x", {"a.py": "1\n"})

    rows = client.get("/api/journal").json()["rows"]
    assert any(r["action"] == "upload" for r in rows)

    stats = client.get("/api/stats").json()
    assert stats["projects"] == 1 and stats["my_projects"] == 1 and stats["actions"] >= 1


def test_delete_project(platform):
    client, platform_main, db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    project = _upload(client, "gone", {"a.py": "1\n"})
    client.post(f"/api/projects/{project['id']}/analyze")

    assert client.delete(f"/api/projects/{project['id']}").json()["ok"] is True
    assert db.get_project(project["id"]) is None
    assert not (platform_main.EXPORT_DIR / f"project_{project['id']}_tree.png").exists()


def test_console_project_with_zip_source_is_analyzable(platform):
    """Проект, присланный из консоли Host ⇄ Pi: source указывает на .zip-файл."""
    client, _main, db, tmp = platform
    _login(client, "pi", "pi")

    zip_path = tmp / "projects" / "src.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(_zip_bytes({"app.py": "print('hi')\n"}))
    project = db.register_project("pi", "src", str(zip_path))

    resp = client.post(f"/api/projects/{project['id']}/analyze")
    assert resp.status_code == 200, resp.text
    assert resp.json()["diagram_code"]


def test_single_file_source_returns_clear_error(platform):
    """Одиночный файл — не проект: API отвечает понятной ошибкой, а не 500."""
    client, _main, db, tmp = platform
    _login(client, "pi", "pi")

    note = tmp / "files" / "note.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("привет")
    project = db.register_project("pi", "note", str(note))

    resp = client.post(f"/api/projects/{project['id']}/analyze")
    assert resp.status_code == 422
    assert "одиночный файл" in resp.json()["detail"]


def test_code_diagram_from_gitdiagram_static(platform):
    """Диаграмма кода: gitdiagram-static даёт узлы для многомодульного проекта."""
    client, _main, db, _tmp = platform
    _login(client, "pi", "pi")

    project = db.register_project("pi", "shop_api", str(SHOP_FIXTURE))
    resp = client.post(f"/api/projects/{project['id']}/analyze")
    assert resp.status_code == 200, resp.text
    assert resp.json()["diagram_kind"] == "code"


def test_permissions_pi_cannot_download_others(platform):
    """Pi не получает чужие материалы; Host — получает (роль что-то значит)."""
    client, platform_main, db, tmp = platform
    _login(client, "vanya_pi", "pi")

    other = db.register_project("host_one", "secret", str(tmp))
    digest = platform_main.EXPORT_DIR / f"project_{other['id']}_digest.txt"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("secret")

    # Pi: чужие данные недоступны
    assert client.get(f"/api/projects/{other['id']}/digest").status_code == 403

    # Host: те же данные доступны
    with TestClient(platform_main.app) as host:
        _login(host, "host_one", "host")
        resp = host.get(f"/api/projects/{other['id']}/digest")
        assert resp.status_code == 200 and resp.text == "secret"


# ---------- раздача основного сайта ----------

@pytest.mark.skipif(not WEB_INDEX.exists(), reason="фронтенд не собран (web/dist)")
def test_spa_is_served_and_api_404(platform):
    client, *_ = platform
    root = client.get("/")
    assert root.status_code == 200 and "text/html" in root.headers["content-type"]
    assert "root" in root.text or "<script" in root.text

    # клиентские маршруты тоже отдают сайт
    assert client.get("/projects").status_code == 200
    assert client.get("/journal").status_code == 200

    # неизвестный /api/* — честный 404, а не index.html
    assert client.get("/api/does-not-exist").status_code == 404
