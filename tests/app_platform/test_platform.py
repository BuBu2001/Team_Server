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
import json
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


# ---------- браузер кода и «пульс» проекта ----------

def test_project_files_browser(platform):
    """Список файлов/папок и содержимое файла (вкладка «Код»)."""
    client, _main, _db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    project = _upload(
        client,
        "files",
        {"README.md": "# hi\n", "src/app.py": "print(1)\n", "src/util.py": "x=2\n"},
    )
    pid = project["id"]

    root = client.get(f"/api/projects/{pid}/files").json()
    by_name = {e["name"]: e for e in root["entries"]}
    assert by_name["src"]["type"] == "dir"
    assert by_name["README.md"]["type"] == "file"

    sub = client.get(f"/api/projects/{pid}/files", params={"path": "src"}).json()
    assert {e["name"] for e in sub["entries"]} == {"app.py", "util.py"}

    file = client.get(
        f"/api/projects/{pid}/file", params={"path": "README.md"}
    ).json()
    assert file["content"] == "# hi\n" and file["binary"] is False

    # обход пути и выход за пределы проекта запрещены
    assert client.get(
        f"/api/projects/{pid}/file", params={"path": "../../secret"}
    ).status_code == 404
    assert client.get(
        f"/api/projects/{pid}/files", params={"path": "../.."}
    ).status_code == 404


def test_project_pulse_and_file_permissions(platform):
    """«Пульс» считает активность и файлы; чужие файлы Pi не видит."""
    client, _main, _db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    project = _upload(client, "pulse-demo", {"a.py": "1\n", "b/c.py": "2\n"})
    pid = project["id"]

    pulse = client.get(f"/api/projects/{pid}/pulse").json()
    assert pulse["files"] == 2 and pulse["dirs"] == 1
    assert any(a["action"] == "upload" for a in pulse["actions"])

    # другой Pi не получает ни файлы, ни пульс чужого проекта
    client.post("/api/auth/logout")
    _login(client, "other_pi", "pi")
    assert client.get(f"/api/projects/{pid}/files").status_code == 403
    assert client.get(f"/api/projects/{pid}/pulse").status_code == 403


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


# ---------- «холст» проекта (сцена Excalidraw, только в системе) ----------

def _scene(pad: str = "") -> str:
    """Минимальная валидная сцена Excalidraw (как её отдаёт serializeAsJSON)."""
    return json.dumps(
        {
            "type": "excalidraw",
            "version": 2,
            "elements": [{"id": "box1", "type": "rectangle"}],
            "appState": {"viewBackgroundColor": "#121212"},
            "files": {},
            "pad": pad,
        },
        separators=(",", ":"),
    )


def _canvas_rows(client: TestClient) -> list[dict]:
    return [r for r in client.get("/api/journal?limit=500").json()["rows"]
            if r.get("action") == "canvas"]


def test_project_canvas_persists_and_is_private(platform):
    """Холст: создаётся, живёт между запросами, чужим Pi недоступен, Host — доступен."""
    client, _main, _db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    project = _upload(client, "canvas-demo", {"a.py": "1\n"})
    pid = project["id"]

    # холст ещё не заводили — пустая сцена, но не ошибка
    empty = client.get(f"/api/projects/{pid}/canvas")
    assert empty.status_code == 200
    data = empty.json()
    assert data["updated_at"] is None and data["updated_by"] is None
    assert json.loads(data["scene"])["elements"] == []

    # сохраняем сцену и читаем её обратно
    scene = _scene()
    saved = client.put(f"/api/projects/{pid}/canvas", json={"scene": scene})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["scene"] == scene
    assert body["updated_by"] == "vanya_pi" and body["updated_at"]

    again = client.get(f"/api/projects/{pid}/canvas").json()
    assert again["scene"] == scene  # холст сохранился
    assert again["updated_at"] == body["updated_at"]

    # повторная запись той же сцены — no-op: один след в журнале, время не едет
    assert client.put(f"/api/projects/{pid}/canvas", json={"scene": scene}).status_code == 200
    assert len(_canvas_rows(client)) == 1

    # холст — сущность платформы: в каталоге репозитория его нет
    names = {e["name"] for e in client.get(f"/api/projects/{pid}/files").json()["entries"]}
    assert "canvas" not in names and "notes" not in names and "scene" not in names

    # чужой Pi не читает и не пишет холст
    client.post("/api/auth/logout")
    _login(client, "other_pi", "pi")
    assert client.get(f"/api/projects/{pid}/canvas").status_code == 403
    assert client.put(
        f"/api/projects/{pid}/canvas", json={"scene": _scene()}
    ).status_code == 403

    # Host видит холст и общий журнал: отказ в журнал не попал
    client.post("/api/auth/logout")
    _login(client, "the_host", "host")
    assert client.get(f"/api/projects/{pid}/canvas").json()["scene"] == scene
    assert len(_canvas_rows(client)) == 1


def test_project_canvas_rejects_broken_scene(platform):
    """В БД попадает только валидная сцена — иначе вкладка падает при открытии."""
    client, _main, _db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    pid = _upload(client, "canvas-broken", {"a.py": "1\n"})["id"]

    for bad in ("{не json", "[1, 2, 3]", '{"elements": {"a": 1}}'):
        resp = client.put(f"/api/projects/{pid}/canvas", json={"scene": bad})
        assert resp.status_code == 422, (bad, resp.status_code, resp.text)

    assert client.get(f"/api/projects/{pid}/canvas").json()["updated_at"] is None
    assert len(_canvas_rows(client)) == 0


def test_project_canvas_size_limit(platform):
    """Холст ограничивается по размеру — это не файловое хранилище."""
    client, main_module, _db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    pid = _upload(client, "canvas-big", {"a.py": "1\n"})["id"]
    limit = main_module.MAX_CANVAS_CHARS

    scene_ok = _scene("x" * (limit - 500))
    assert len(scene_ok) <= limit
    assert client.put(
        f"/api/projects/{pid}/canvas", json={"scene": scene_ok}
    ).status_code == 200, limit

    scene_big = _scene("x" * (limit + 1000))
    assert len(scene_big) > limit
    assert client.put(
        f"/api/projects/{pid}/canvas", json={"scene": scene_big}
    ).status_code == 413

    # содержимое не подменилось
    assert client.get(f"/api/projects/{pid}/canvas").json()["scene"] == scene_ok


def test_project_canvas_removed_with_project(platform):
    """Удаление проекта подчищает его холст."""
    client, _main, _db, _tmp = platform
    _login(client, "vanya_pi", "host")
    project = _upload(client, "canvas-gone", {"a.py": "1\n"})
    pid = project["id"]
    client.put(f"/api/projects/{pid}/canvas", json={"scene": _scene()})

    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}/canvas").status_code == 404  # проекта нет


# =====================================================================
#  «Песочница»: разговор с ИИ (Qwen) + правка файлов из ответа
# =====================================================================


def test_sandbox_requires_login(platform):
    """Без сессии все песочные роуты отвечают 401 (браузер не поднимается)."""
    client, _main, _db, _tmp = platform
    assert client.get("/api/sandbox/status").status_code == 401
    assert client.post("/api/sandbox/browser").status_code == 401
    assert client.post("/api/sandbox/ask", json={"prompt": "привет"}).status_code == 401
    assert client.post(
        "/api/sandbox/apply", json={"project_id": 1, "path": "a.py", "content": "1"}
    ).status_code == 401


def test_sandbox_status_closed_by_default(platform):
    """До первого открытия статус честно говорит «браузер закрыт»."""
    client, _main, _db, _tmp = platform
    _login(client, "nick_sandbox", "host")
    resp = client.get("/api/sandbox/status")
    assert resp.status_code == 200
    assert resp.json()["open"] is False
    assert resp.json()["logged_in"] is None


def test_sandbox_ask_validation(platform):
    """Пустой вопрос — 422, слишком длинный — 413, чужой проект — 403."""
    client, _main, _db, _tmp = platform
    _login(client, "nick_sandbox", "host")
    project = _upload(client, "sandbox-ask", {"a.py": "print(1)\n"})
    pid = project["id"]

    assert client.post("/api/sandbox/ask", json={"prompt": "   "}).status_code == 422
    assert (
        client.post(
            "/api/sandbox/ask", json={"prompt": "х" * 20_001, "project_id": pid}
        ).status_code
        == 413
    )

    # доступ к чужому проекту закрыт и здесь
    client.post("/api/auth/login", json={"nick": "other_pi"})
    assert (
        client.post(
            "/api/sandbox/ask", json={"prompt": "привет", "project_id": pid}
        ).status_code
        == 403
    )


def test_sandbox_ask_sends_project_context(platform, monkeypatch):
    """Вопрос уходит с контекстом файлов проекта, ответ разбирается на правки."""
    from app_platform import qwen

    client, _main, _db, _tmp = platform
    _login(client, "nick_sandbox", "host")
    project = _upload(client, "sandbox-ctx", {"main.py": "print('hi')\n"})
    pid = project["id"]

    seen: dict = {}

    async def fake_ask(prompt: str, context: str = "", timeout_s: int = 0) -> str:
        seen["prompt"] = prompt
        seen["context"] = context
        return (
            "Вот исправленный файл:\n"
            "ФАЙЛ: main.py\n"
            "print('hello')\n"
            "КОНЕЦ-ФАЙЛА\n"
            "Готово."
        )

    monkeypatch.setattr(qwen, "ask", fake_ask)
    resp = client.post(
        "/api/sandbox/ask", json={"prompt": "почини", "project_id": pid}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project"] == "sandbox-ctx"
    # контекст содержит файл проекта и инструкцию про маркеры правок
    assert "main.py" in seen["context"]
    assert "КОНЕЦ-ФАЙЛА" in seen["context"]
    # правка из ответа разобрана и отдана фронту
    assert body["edits"] == [{"path": "main.py", "content": "print('hello')\n"}]

    # вопрос ушёл в журнал
    actions = [a for a in client.get("/api/journal").json()["rows"] if a["action"] == "sandbox"]
    assert any("вопрос ИИ" in (a["details"] or "") for a in actions)


def test_sandbox_ask_without_project_has_no_edits(platform, monkeypatch):
    """Общий вопрос без проекта: контекста нет, правки не предлагаются."""
    from app_platform import qwen

    client, _main, _db, _tmp = platform
    _login(client, "nick_sandbox", "host")

    async def fake_ask(prompt: str, context: str = "", timeout_s: int = 0) -> str:
        assert context == ""
        return "Привет! Я просто отвечаю."

    monkeypatch.setattr(qwen, "ask", fake_ask)
    resp = client.post("/api/sandbox/ask", json={"prompt": "привет"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["edits"] == []
    assert resp.json()["answer"].startswith("Привет")


def test_sandbox_apply_writes_file(platform):
    """«Применить» перезаписывает файл проекта и пишет запись в журнал."""
    client, _main, _db, _tmp = platform
    _login(client, "nick_sandbox", "host")
    project = _upload(client, "sandbox-apply", {"main.py": "old = 1\n"})
    pid = project["id"]

    resp = client.post(
        "/api/sandbox/apply",
        json={"project_id": pid, "path": "main.py", "content": "new = 2\n"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"path": "main.py", "bytes": len("new = 2\n")}

    # файл реально изменился — читаем через файловый роут
    body = client.get(f"/api/projects/{pid}/file?path=main.py").json()
    assert body["content"] == "new = 2\n"

    # запись ушла в журнал
    actions = [a for a in client.get("/api/journal").json()["rows"] if a["action"] == "sandbox"]
    assert any("применил правку" in (a["details"] or "") for a in actions)


def test_sandbox_apply_rejects_bad_paths(platform):
    """Путь наружу из проекта, папка и несуществующий файл — отказ."""
    client, _main, _db, _tmp = platform
    _login(client, "nick_sandbox", "host")
    project = _upload(client, "sandbox-paths", {"main.py": "x = 1\n"})
    pid = project["id"]

    def apply(path: str):
        return client.post(
            "/api/sandbox/apply",
            json={"project_id": pid, "path": path, "content": "y\n"},
        )

    assert apply("../evil.py").status_code == 400  # выход за корень
    assert apply("").status_code == 400  # пустой путь — это сам корень (папка)
    assert apply("sub/missing.py").status_code == 404  # файла нет
    assert client.get(f"/api/projects/{pid}/file?path=main.py").json()["content"] == "x = 1\n"


def test_sandbox_apply_denied_for_stranger(platform):
    """Посторонний Pi (не владелец) не может править файлы проекта."""
    client, _main, _db, _tmp = platform
    _login(client, "owner_nick", "host")
    project = _upload(client, "sandbox-denied", {"main.py": "a = 1\n"})
    pid = project["id"]

    client.post("/api/auth/login", json={"nick": "stranger_pi"})
    resp = client.post(
        "/api/sandbox/apply",
        json={"project_id": pid, "path": "main.py", "content": "hack\n"},
    )
    assert resp.status_code in (403, 404)  # доступ к проекту закрыт вовсе
    assert resp.status_code != 200


def test_parse_file_edits_blocks():
    """Разбор маркеров «ФАЙЛ: …» / «КОНЕЦ-ФАЙЛА» в ответе ИИ."""
    from app_platform import qwen

    # два блока подряд
    answer = (
        "Сначала одно:\n"
        "ФАЙЛ: src/a.py\n"
        "line1\n"
        "line2\n"
        "КОНЕЦ-ФАЙЛА\n"
        "Потом другое:\n"
        "ФАЙЛ: b.py\n"
        "print(42)\n"
        "КОНЕЦ-ФАЙЛА\n"
        "Конец ответа."
    )
    assert qwen.parse_file_edits(answer) == [
        {"path": "src/a.py", "content": "line1\nline2\n"},
        {"path": "b.py", "content": "print(42)\n"},
    ]

    # без КОНЦА-ФАЙЛА блок тянется до следующего маркера
    no_end = "ФАЙЛ: a.py\nX = 1\nФАЙЛ: b.py\nY = 2\nКОНЕЦ-ФАЙЛА"
    assert qwen.parse_file_edits(no_end) == [
        {"path": "a.py", "content": "X = 1\n"},
        {"path": "b.py", "content": "Y = 2\n"},
    ]

    # обычный ответ без маркеров — пусто, а не мусор
    assert qwen.parse_file_edits("Просто ответ, правок нет.") == []
    assert qwen.parse_file_edits("") == []
    # маркер без содержимого пропускается
    assert qwen.parse_file_edits("ФАЙЛ: empty.py\n\nКОНЕЦ-ФАЙЛА") == []


def test_build_project_context_captures_files(tmp_path):
    """Контекст собирает файлы, пропускает .git/бинарники и несёт правила правок."""
    from app_platform import qwen

    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("print('ctx')\n", encoding="utf-8")
    (root / "big.py").write_text("z" * (qwen.MAX_FILE_CHARS + 500), encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02binary")

    ctx = qwen.build_project_context(root, "проект-контекст")
    assert "проект-контекст" in ctx
    assert "### app.py" in ctx and "print('ctx')" in ctx
    assert "КОНЕЦ-ФАЙЛА" in ctx  # инструкция про маркеры правок
    assert "… (файл обрезан)" in ctx  # большой файл порезан
    assert "### .git/config" not in ctx  # служебная папка не тащится
    assert "\x00" not in ctx  # бинарник не тащится
    assert "пропущено служебных/бинарных" in ctx

    # пустая папка — пустой контекст
    empty = tmp_path / "empty"
    empty.mkdir()
    assert qwen.build_project_context(empty, "пусто") == ""


def test_qwen_executable_env_override(monkeypatch, tmp_path):
    """TEAM_SERVER_BROWSER задаёт бинарник браузера вручную."""
    from app_platform import qwen

    fake = tmp_path / "chrome-stub"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("TEAM_SERVER_BROWSER", str(fake))
    assert qwen._executable() == str(fake)
