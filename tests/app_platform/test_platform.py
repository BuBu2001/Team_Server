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
import re
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


def test_download_source_zip_and_single_file(platform):
    """Скачивание исходника (zip, кнопка у Digest) и одного файла (в просмотре)."""
    client, _main, _db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    project = _upload(
        client, "dl-demo", {"README.md": "# hi\n", "src/app.py": "print(1)\n"}
    )
    pid = project["id"]

    # исходник: zip со всеми файлами и исходной структурой каталогов
    src = client.get(f"/api/projects/{pid}/source")
    assert src.status_code == 200, src.text
    assert src.headers["content-type"].startswith("application/zip")
    assert 'filename="dl-demo.zip"' in src.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(src.content)) as zf:
        assert set(zf.namelist()) == {"README.md", "src/app.py"}
        assert zf.read("src/app.py") == b"print(1)\n"

    # один файл — полный оригинал с принудительным скачиванием
    got = client.get(f"/api/projects/{pid}/download", params={"path": "src/app.py"})
    assert got.status_code == 200 and got.content == b"print(1)\n"
    assert 'attachment; filename="app.py"' in got.headers["content-disposition"]

    # выход за пределы проекта запрещён
    assert client.get(
        f"/api/projects/{pid}/download", params={"path": "../../etc/passwd"}
    ).status_code == 404

    # обе выгрузки попали в журнал (вкладка «Пульс»)
    pulse = client.get(f"/api/projects/{pid}/pulse").json()
    downloads = [a for a in pulse["actions"] if a["action"] == "download"]
    assert len(downloads) == 2


def test_binary_file_view_reports_kind_hex_and_strings(platform):
    """Бинарник в браузере кода: тип по магии, hex-дамп и вшитые строки."""
    client, *_ = platform
    _login(client, "vanya_pi", "pi")
    elf = (
        b"\x7fELF\x02\x01\x01"
        + b"\x00" * 9
        + b"built from src/main.c: antiworld renderer v1"
        + b"\x00" * 8
    )
    project = _upload(client, "bin-demo", {"antiworld": elf, "note.txt": "текст\n"})
    pid = project["id"]

    body = client.get(f"/api/projects/{pid}/file", params={"path": "antiworld"}).json()
    assert body["binary"] is True
    assert "ELF" in body["kind"] and "64" in body["kind"]
    assert body["hex"].startswith("00000000  7f 45 4c 46")
    assert any("antiworld renderer" in s for s in body["strings"])

    # обычный текстовый файл рядом — как раньше показывает код
    txt = client.get(f"/api/projects/{pid}/file", params={"path": "note.txt"}).json()
    assert txt["binary"] is False and txt["content"] == "текст\n"
    assert "hex" not in txt


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
    """Общий вопрос без проекта: файлов нет и правки не предлагаются —
    в контексте только инструкция про создание репозитория."""
    from app_platform import qwen

    client, _main, _db, _tmp = platform
    _login(client, "nick_sandbox", "host")

    async def fake_ask(prompt: str, context: str = "", timeout_s: int = 0) -> str:
        # файлов проекта и правок нет — только CREATE_RULES в самом конце
        assert "main.py" not in context
        assert "КОНЕЦ-ФАЙЛА" not in context
        assert "ИНСТРУМЕНТ: создать" in context
        return "Привет! Я просто отвечаю."

    monkeypatch.setattr(qwen, "ask", fake_ask)
    resp = client.post("/api/sandbox/ask", json={"prompt": "привет"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["edits"] == []
    assert resp.json()["answer"].startswith("Привет")


def test_parse_agent_calls_blocks():
    """Разбор блоков «ИНСТРУМЕНТ: … КОНЕЦ-ИНСТРУМЕНТА» в ответе ИИ."""
    from app_platform import qwen

    # без блоков — пусто (это обычный финальный ответ)
    assert qwen.parse_agent_calls("Обычный ответ без инструментов.") == []

    # один блок: имя инструмента и путь
    answer = "Смотрю проект.\nИНСТРУМЕНТ: список\nФАЙЛ: .\nКОНЕЦ-ИНСТРУМЕНТА\nГотово."
    assert qwen.parse_agent_calls(answer) == [
        {"tool": "список", "path": ".", "query": "", "name": "", "content": ""}
    ]

    # блок «создать»: имя репозитория берётся из строки «НАЗВАНИЕ:»
    answer = "ИНСТРУМЕНТ: создать\nНАЗВАНИЕ: My Repo\nКОНЕЦ-ИНСТРУМЕНТА"
    calls = qwen.parse_agent_calls(answer)
    assert [c["tool"] for c in calls] == ["создать"]
    assert calls[0]["name"] == "My Repo"

    # два блока подряд: у записи снимается кодовый блок, у поиска — запрос
    answer = (
        "Правлю:\n"
        "ИНСТРУМЕНТ: запись\n"
        "ФАЙЛ: pkg/mod.py\n"
        "```python\nx = 1\n```\n"
        "КОНЕЦ-ИНСТРУМЕНТА\n"
        "ИНСТРУМЕНТ: поиск\n"
        "ЗАПРОС: x = 1\n"
        "КОНЕЦ-ИНСТРУМЕНТА\n"
    )
    calls = qwen.parse_agent_calls(answer)
    assert [c["tool"] for c in calls] == ["запись", "поиск"]
    assert calls[0]["path"] == "pkg/mod.py"
    assert calls[0]["content"] == "x = 1\n"
    assert calls[1]["query"] == "x = 1"

    # strip_tool_calls: блок «создать» вырезается целиком, чужие блоки — остаются
    mixed = (
        "Готово.\n\n"
        "ИНСТРУМЕНТ: создать\nНАЗВАНИЕ: r1\nКОНЕЦ-ИНСТРУМЕНТА\n\n"
        "И файл:\n"
        "ИНСТРУМЕНТ: чтение\nФАЙЛ: a.py\nКОНЕЦ-ИНСТРУМЕНТА\n"
    )
    stripped = qwen.strip_tool_calls(mixed, {"создать"})
    assert "НАЗВАНИЕ: r1" not in stripped
    assert "ИНСТРУМЕНТ: чтение" in stripped
    assert stripped.startswith("Готово.")


def test_qwen_profile_hardening(tmp_path):
    """Профиль браузера: папка только для владельца, пароли не сохраняются."""
    import stat

    from app_platform import qwen

    profile = tmp_path / "qwen_profile"
    default = profile / "Default"
    default.mkdir(parents=True)
    prefs_file = default / "Preferences"
    prefs_file.write_text(
        json.dumps(
            {"credentials_enable_service": True, "browser": {"show_home_button": True}}
        ),
        encoding="utf-8",
    )

    qwen._harden_profile(profile)

    # папка профиля — только владелец (куки со стороны не читаются)
    assert stat.S_IMODE(profile.stat().st_mode) == 0o700
    # менеджер паролей выключен, чужие настройки целы
    prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
    assert prefs["credentials_enable_service"] is False
    assert prefs["profile"]["password_manager_enabled"] is False
    assert prefs["browser"]["show_home_button"] is True
    assert stat.S_IMODE(prefs_file.stat().st_mode) == 0o600

    # профиль ещё не создавался — хардинг просто создаёт файл со своими ключами
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    qwen._harden_profile(fresh)
    prefs = json.loads((fresh / "Default" / "Preferences").read_text(encoding="utf-8"))
    assert prefs["credentials_enable_service"] is False
    assert prefs["profile"]["password_manager_enabled"] is False


def test_safe_url_hides_query():
    """В логи не утекают query-параметры (OAuth-коды, state, токены)."""
    from app_platform import qwen

    assert qwen._safe_url("https://chat.qwen.ai/c/1?code=SECRET&state=x") == (
        "https://chat.qwen.ai/c/1"
    )
    assert qwen._safe_url("https://chat.qwen.ai/") == "https://chat.qwen.ai/"
    assert qwen._safe_url("") == ""
    assert qwen._safe_url(None) == ""  # type: ignore[arg-type]


def test_login_from_new_ip_forgets_profile(platform, monkeypatch):
    """Вход с нового IP стирает профиль браузера; с того же — ничего не трогается."""
    from app_platform import qwen

    client, _main, _db, _tmp = platform

    # фиктивный профиль со «следами прошлого входа» + подмена пути в тестах
    profile = _tmp / "qwen_profile"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies").write_bytes(b"old-session")
    monkeypatch.setattr(qwen, "profile_dir", lambda: profile)

    closed: list = []

    async def fake_close():
        closed.append(True)

    monkeypatch.setattr(qwen, "close_browser", fake_close)

    def login(ip: str, nick: str = "ip_user"):
        return client.post(
            "/api/auth/login",
            json={"nick": nick},
            headers={"X-Forwarded-For": ip},
        )

    # первый вход: IP записан, стирать нечего — профиль цел
    assert login("10.0.0.1").status_code == 200
    assert not closed and profile.exists()

    # тот же IP: ничего не забывается
    resp = login("10.0.0.1")
    assert resp.status_code == 200
    assert resp.json()["profile_forgotten"] is False
    assert not closed and profile.exists()

    # новый IP: данные прошлого входа стёрты (куки/история/пароли)
    resp = login("203.0.113.5")
    assert resp.status_code == 200, resp.text
    assert resp.json()["profile_forgotten"] is True
    assert closed and not profile.exists()

    # и снова этот же IP: уже ничего не стирается
    resp = login("203.0.113.5")
    assert resp.json()["profile_forgotten"] is False
    assert len(closed) == 1

    # журнал объясняет, почему выбросило из аккаунта Qwen
    actions = [
        a for a in client.get("/api/journal").json()["rows"] if a["action"] == "login"
    ]
    assert any("нового IP 203.0.113.5" in (a["details"] or "") for a in actions)


# ---------- аудит безопасности: критичные дыры (C-3, C-4, C-5, C-6, H-6, M-2) ----------


def test_upload_filename_traversal_neutralized(platform):
    """Имя файла из multipart не выводит запись за пределы проекта (C-3).

    «../» и абсолютный путь срезаются до basename и записываются безопасным
    именем внутри каталога проекта; мусорные имена отклоняются.
    """
    client, _main, _db, tmp = platform
    _login(client, "vanya_pi", "pi")

    resp = client.post(
        "/api/projects",
        data={"name": "trav"},
        files={"archive": ("../../../../tmp/pwn_upload", b"data", "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    assert not Path("/tmp/pwn_upload").exists()          # за пределы не вышли
    assert (tmp / "projects" / "vanya_pi__trav" / "pwn_upload").read_bytes() == b"data"

    resp = client.post(
        "/api/projects",
        data={"name": "trav2"},
        files={"archive": ("/tmp/pwn_upload_abs", b"data2", "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    assert not Path("/tmp/pwn_upload_abs").exists()
    assert (
        tmp / "projects" / "vanya_pi__trav2" / "pwn_upload_abs"
    ).read_bytes() == b"data2"

    resp = client.post(
        "/api/projects",
        data={"name": "trav3"},
        files={"archive": ("..\\..\\evil.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 422, resp.text

    # служебный файл-указатель платформы подменить нельзя
    resp = client.post(
        "/api/projects",
        data={"name": "trav4"},
        files={"archive": (".source_url", b"/etc", "text/plain")},
    )
    assert resp.status_code == 422, resp.text

    # юникод-имя по-прежнему работает — русские названия файлов не ломаем
    resp = client.post(
        "/api/projects",
        data={"name": "trav5"},
        files={"archive": ("заметки.txt", "привет".encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    assert (
        tmp / "projects" / "vanya_pi__trav5" / "заметки.txt"
    ).read_text(encoding="utf-8") == "привет"


def test_source_field_only_allows_git_hosts(platform):
    """Поле source: только https-URL известного git-хоста (C-4)."""
    client, _main, _db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    for bad in (
        "/etc",
        "file:///etc",
        "http://github.com/x/y",
        "https://evil.example/x/y",
        "https://git.internal.corp/r",
    ):
        resp = client.post("/api/projects", data={"name": "leak", "source": bad})
        assert resp.status_code == 422, (bad, resp.text)
    ok = client.post(
        "/api/projects",
        data={"name": "okrepo", "source": "https://github.com/octocat/Hello-World"},
    )
    assert ok.status_code == 200, ok.text


def test_source_url_inside_zip_revalidated(platform):
    """`.source_url`, попавший в папку архивом, тоже валидируется (C-4)."""
    client, _main, _db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(".source_url", "/etc")
    resp = client.post(
        "/api/projects",
        data={"name": "zipurl"},
        files={"archive": ("zipurl.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["project"]["id"]
    resp = client.post(f"/api/projects/{pid}/analyze")
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]  # отказ, а не анализ /etc


def test_zip_bomb_rejected(platform):
    """Zip-бомба отклоняется по коэффициенту сжатия до распаковки (H-6)."""
    client, _main, _db, _tmp = platform
    _login(client, "vanya_pi", "pi")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(40):
            zf.writestr(f"bomb_{i}.bin", b"A" * (1024 * 1024))  # 40 МБ сплошняком
    data = buf.getvalue()
    assert len(data) < 200_000  # сжато почти в ноль → коэффициент ~1000x
    resp = client.post(
        "/api/projects",
        data={"name": "bomb"},
        files={"archive": ("bomb.zip", data, "application/zip")},
    )
    assert resp.status_code == 422, resp.text
    assert "сжатия" in resp.json()["detail"] or "бомб" in resp.json()["detail"]


def test_cryless_force_requires_repo_token(platform):
    """--force без токена не даёт захватить чужой репозиторий (C-6)."""
    client, _main, _db, _tmp = platform
    first = client.post(
        "/api/cryless/repositories",
        json={"name": "privrepo"},
        headers={"X-Cryless-User": "victim"},
    )
    assert first.status_code == 201, first.text
    token = first.json()["token"]

    # попытка захвата: force с тем же ником, но без токена — отказ
    taken = client.post(
        "/api/cryless/repositories",
        json={"name": "privrepo", "force": True},
        headers={"X-Cryless-User": "victim"},
    )
    assert taken.status_code == 403, taken.text

    # с неверным токеном — тоже отказ
    bad = client.post(
        "/api/cryless/repositories",
        json={"name": "privrepo", "force": True},
        headers={"X-Cryless-User": "victim", "Authorization": "Bearer wrong"},
    )
    assert bad.status_code == 403, bad.text

    # владелец со своим токеном — пересоздаёт и получает новый токен
    ok = client.post(
        "/api/cryless/repositories",
        json={"name": "privrepo", "force": True},
        headers={"X-Cryless-User": "victim", "Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["token"] != token

    # повторное создание без force — 409, и id больше не раскрывается
    dup = client.post(
        "/api/cryless/repositories",
        json={"name": "privrepo"},
        headers={"X-Cryless-User": "victim"},
    )
    assert dup.status_code == 409
    assert "id" not in dup.json()


def test_content_disposition_sanitized(platform):
    """Имя проекта не попадает в Content-Disposition сырым (M-2)."""
    client, main_mod, db, tmp = platform
    _login(client, "vanya_pi", "pi")
    user = db.get_user_by_name("vanya_pi")
    proj_dir = tmp / "p_disp"
    proj_dir.mkdir()
    proj = db.add_project(user["id"], 'x"; filename=pwn.exe', str(proj_dir))
    digest = main_mod.EXPORT_DIR / f"project_{proj['id']}_digest.txt"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("digest", encoding="utf-8")
    resp = client.get(f"/api/projects/{proj['id']}/digest")
    assert resp.status_code == 200
    disp = resp.headers.get("content-disposition", "")
    # внутри кавычек не может быть кавычек и «;» — «выход» из заголовка
    # невозможен, имя безопасно
    assert re.fullmatch(r'inline; filename="[^";]*"', disp), disp


def test_tcp_host_sub_allowlist(tmp_path):
    """Разделы TCP-хоста: `sub` из сети не сдвигает корень safe_join (C-5b)."""
    from getting_started import core

    ws = core.HostWorkspace(tmp_path)
    assert ws.path_for("files", "a.txt") == (tmp_path / "files" / "a.txt").resolve()
    with pytest.raises(ValueError):
        ws.path_for("../../../etc", "passwd")
    with pytest.raises(ValueError):
        ws.path_for("/etc", "passwd")


def test_tcp_session_user_identity():
    """Идентичность Pi — из handshake; грязные значения вычищаются (C-5c)."""
    from getting_started import core

    assert core._session_user(None) == "pi"
    assert core._session_user({}) == "pi"
    assert core._session_user({"user": "vanya_pi"}) == "vanya_pi"
    assert core._session_user({"user": "../../evil; drop"}) == "evildrop"


def test_run_tool_writes_and_stays_inside_root(tmp_path):
    """«запись» пишет файл сразу; чтение/список/поиск работают; ../ отсекается."""
    from app_platform import qwen

    root = tmp_path.resolve()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")

    def tool(**call):
        base = {"tool": "", "path": "", "query": "", "content": ""}
        base.update(call)
        return qwen.run_tool(base, root)

    # запись создаёт файл вместе с папками — без «Применить»
    _res, note = tool(tool="запись", path="pkg/new.py", content="y = 2\n")
    assert (root / "pkg" / "new.py").read_text(encoding="utf-8") == "y = 2\n"
    assert note.startswith("записан pkg/new.py")

    res, _ = tool(tool="чтение", path="a.py")
    assert res == "x = 1\n"
    res, _ = tool(tool="список", path=".")
    assert "a.py" in res and "pkg/" in res
    res, _ = tool(tool="поиск", query="x = 1")
    assert "a.py:1:" in res

    # путь наружу — ошибка в журнал, файл не создаётся
    res, note = tool(tool="запись", path="../evil.py", content="hack\n")
    assert note.startswith("ошибка") and "Ошибка" in res
    assert not (root.parent / "evil.py").exists()


def test_sandbox_agent_runs_loop_and_writes(platform, monkeypatch):
    """Режим агента: цикл инструментов крутится, файл пишется сразу, есть журнал."""
    from app_platform import qwen

    # владелец-пи (не Host) тоже может работать агентом
    client, _main, _db, _tmp = platform
    _login(client, "owner_nick", "pi")
    project = _upload(client, "sandbox-agent", {"main.py": "old = 1\n"})
    pid = project["id"]

    turns = iter(
        [
            # первый ответ модели: блок «запись» (замаскированный под ответ)
            "Правлю файл.\n"
            "ИНСТРУМЕНТ: запись\n"
            "ФАЙЛ: main.py\n"
            "```python\nnew = 2\n```\n"
            "КОНЕЦ-ИНСТРУМЕНТА\n",
            # второй ход: финальный ответ без блоков — цикл заканчивается
            "Готово: файл исправлен.",
        ]
    )

    async def fake_turn(page, text: str, timeout_s: int) -> str:
        # первый ход — вопрос с инструкцией агента, второй — результат инструмента
        if "Результаты инструментов" in text:
            assert "Файл main.py записан" in text
        return next(turns)

    async def fake_page():
        return None

    site_mode: dict = {}

    async def fake_site_mode(page, on: bool) -> bool:
        site_mode["on"] = on
        return on

    monkeypatch.setattr(qwen, "_turn", fake_turn)
    monkeypatch.setattr(qwen, "_ensure_chat_page", fake_page)
    monkeypatch.setattr(qwen, "_set_agent_mode", fake_site_mode)

    resp = client.post(
        "/api/sandbox/ask", json={"prompt": "почини", "project_id": pid, "agent": True}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edits"] == []  # в агенте «Применить» не нужен
    assert body["answer"] == "Готово: файл исправлен."
    assert [s["tool"] for s in body["agent_log"]] == ["запись"]
    assert body["agent_log"][0]["path"] == "main.py"
    assert body["agent_log"][0]["note"].startswith("записан main.py")
    # перед циклом на сайте Qwen включается родный режим агента
    assert site_mode["on"] is True

    # файл проекта реально изменился — читаем через файловый роут
    f = client.get(f"/api/projects/{pid}/file?path=main.py").json()
    assert f["content"] == "new = 2\n"

    # действия агента ушли в журнал
    actions = [a for a in client.get("/api/journal").json()["rows"] if a["action"] == "sandbox"]
    assert any("агент ИИ записал main.py" in (a["details"] or "") for a in actions)


def test_sandbox_agent_rights(platform):
    """Чужой проект агенту не берётся (403/404); без проекта — см. следующий тест."""
    client, _main, _db, _tmp = platform
    _login(client, "nick_sandbox", "host")
    project = _upload(client, "sandbox-agent-perm", {"main.py": "x = 1\n"})
    pid = project["id"]

    # без проекта агент теперь работает — он создаёт репозиторий инструментом
    # «создать» (это test_sandbox_agent_creates_repo_without_project); а чужой
    # проект взять нельзя — доступ закрыт вовсе
    client.post("/api/auth/login", json={"nick": "stranger_pi"})
    resp = client.post(
        "/api/sandbox/ask", json={"prompt": "почини", "project_id": pid, "agent": True}
    )
    assert resp.status_code in (403, 404)  # доступ к проекту закрыт вовсе
    assert resp.status_code != 200


def test_sandbox_agent_creates_repo_without_project(platform, monkeypatch):
    """Агент без выбранного проекта создаёт репозиторий и пишет файлы уже в него."""
    from app_platform import qwen

    client, _main, _db, _tmp = platform
    _login(client, "owner_nick", "pi")

    turns = iter(
        [
            # первый ответ: блок «создать» — проекта ещё нет вовсе
            "Создаю репозиторий.\n"
            "ИНСТРУМЕНТ: создать\n"
            "НАЗВАНИЕ: agent-new-repo\n"
            "КОНЕЦ-ИНСТРУМЕНТА\n",
            # второй ход: файл пишется уже в новый репозиторий
            "Записываю файл.\n"
            "ИНСТРУМЕНТ: запись\n"
            "ФАЙЛ: main.py\n"
            "```python\nprint('hi')\n```\n"
            "КОНЕЦ-ИНСТРУМЕНТА\n",
            # третий: финальный ответ без блоков — цикл заканчивается
            "Готово: репозиторий создан.",
        ]
    )
    first: dict = {}

    async def fake_turn(page, text: str, timeout_s: int) -> str:
        if not first:
            first["text"] = text
            # без проекта агенту прямо сказано, что доступно только «создать»
            assert "Проект не выбран" in text
        return next(turns)

    async def fake_page():
        return None

    async def fake_site_mode(page, on: bool) -> bool:
        return on

    monkeypatch.setattr(qwen, "_turn", fake_turn)
    monkeypatch.setattr(qwen, "_ensure_chat_page", fake_page)
    monkeypatch.setattr(qwen, "_set_agent_mode", fake_site_mode)

    resp = client.post(
        "/api/sandbox/ask",
        json={"prompt": "создай репозиторий agent-new-repo", "agent": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project"] == ""
    assert body["created"]["name"] == "agent-new-repo"
    assert [s["tool"] for s in body["agent_log"]] == ["создать", "запись"]
    assert body["agent_log"][0]["note"].startswith("создан репозиторий")

    # репозиторий реально появился в списке, файл записан именно в него
    names = {p["name"] for p in client.get("/api/projects").json()["projects"]}
    assert "agent-new-repo" in names
    f = client.get(
        f"/api/projects/{body['created']['id']}/file", params={"path": "main.py"}
    ).json()
    assert f["content"] == "print('hi')\n"

    # журнал: ИИ создал репозиторий
    actions = [
        a for a in client.get("/api/journal").json()["rows"] if a["action"] == "sandbox"
    ]
    assert any("создал репозиторий «agent-new-repo»" in (a["details"] or "") for a in actions)


def test_sandbox_plain_chat_creates_repo(platform, monkeypatch):
    """Обычный чат (без проекта): блок «создать» выполняется, сырой блок вычищен."""
    from app_platform import qwen

    client, _main, _db, _tmp = platform
    _login(client, "owner_nick", "pi")

    async def fake_ask(prompt: str, context: str = "", timeout_s: int = 0) -> str:
        # инструкция про создание уходит в промпт и стоит в самом конце
        # (после файлов проекта) — прочитанная последней, она надёжнее
        assert prompt == "создай репозиторий plain-repo"
        assert "ИНСТРУМЕНТ: создать" in context
        assert context.strip().endswith(qwen.CREATE_RULES.strip())
        return (
            "Создаю репозиторий для тебя.\n\n"
            "ИНСТРУМЕНТ: создать\n"
            "НАЗВАНИЕ: plain-repo\n"
            "КОНЕЦ-ИНСТРУМЕНТА\n"
        )

    monkeypatch.setattr(qwen, "ask", fake_ask)

    resp = client.post("/api/sandbox/ask", json={"prompt": "создай репозиторий plain-repo"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"]["name"] == "plain-repo"
    # сырой протокол из ответа вычищен — пользователь видит только итог
    assert "ИНСТРУМЕНТ" not in body["answer"]
    assert "plain-repo" in body["answer"]

    names = {p["name"] for p in client.get("/api/projects").json()["projects"]}
    assert "plain-repo" in names


def test_sandbox_mode_switches_site_qwen(platform, monkeypatch):
    """Тумблер щёлкает родным «Agent Mode» на странице Qwen; сбой — 503."""
    from app_platform import qwen

    client, _main, _db, _tmp = platform
    assert client.post("/api/sandbox/mode", json={"agent": True}).status_code == 401

    _login(client, "nick_sandbox", "host")
    seen: dict = {}

    async def fake_mode(on: bool) -> bool:
        seen["on"] = on
        return on

    monkeypatch.setattr(qwen, "set_site_agent_mode", fake_mode)
    resp = client.post("/api/sandbox/mode", json={"agent": True})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"agent": True}
    assert seen["on"] is True

    # ошибка браузера/вёрстки Qwen — понятный текст и 503, фронт покажет причину
    async def boom(on: bool) -> bool:
        raise qwen.QwenError("Меню режимов Qwen не открылось — повторите попытку.")

    monkeypatch.setattr(qwen, "set_site_agent_mode", boom)
    resp = client.post("/api/sandbox/mode", json={"agent": False})
    assert resp.status_code == 503
    assert "Меню режимов" in resp.json()["detail"]

    # включение ушло в журнал
    monkeypatch.setattr(qwen, "set_site_agent_mode", fake_mode)
    client.post("/api/sandbox/mode", json={"agent": True})
    actions = [a for a in client.get("/api/journal").json()["rows"] if a["action"] == "sandbox"]
    assert any("режим агента на сайте Qwen" in (a["details"] or "") for a in actions)


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


def test_sandbox_reset_starts_new_dialog(platform, monkeypatch):
    """Сброс диалога: уходит в qwen.new_chat(), результат — в журнал."""
    from app_platform import qwen

    client, _main, _db, _tmp = platform
    assert client.post("/api/sandbox/reset").status_code == 401

    _login(client, "nick_sandbox", "host")
    called: dict = {}

    async def fake_new_chat():
        called["yes"] = True
        return {"open": True, "url": "https://chat.qwen.ai/", "logged_in": True}

    monkeypatch.setattr(qwen, "new_chat", fake_new_chat)
    resp = client.post("/api/sandbox/reset")
    assert resp.status_code == 200, resp.text
    assert resp.json()["open"] is True
    assert called.get("yes")

    actions = [a for a in client.get("/api/journal").json()["rows"] if a["action"] == "sandbox"]
    assert any("новый диалог" in (a["details"] or "") for a in actions)
