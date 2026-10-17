"""Team Server — основной сайт (React SPA) и его JSON-API.

Архитектура
-----------
Сайт разделён на две части:

* **Один основной сайт** — собранное React-приложение (``web/dist``),
  которое раздаётся по всем «человеческим» адресам (``/``, ``/projects``,
  ``/journal`` …). Это единственный интерфейс для пользователя.
* **JSON-API** (``/api/*``) — договор между сайтом и сервером: сессия по нику,
  проекты, анализ, диiagramмы, журнал, произвольные репозитории.

Служебные разделы остаются рядом и не конфликтуют с SPA:

* ``/site``  — интегрированный сайт gitingest (просмотр/анализ репозиториев,
  ``/site/docs`` — Swagger API);
* ``/api/cryless`` — HTTP-API библиотеки команд cryless (см. cryless_api.py).

Роли
----
* **Host** — сервер этой сети: видит все проекты, журнал команды и материалы
  любого проекта;
* **Pi** — участник: загружает свои проекты и получает их результаты.
  Скачивать чужие материалы Pi не может (``can_access_project``).

Пути данных можно переопределять переменными окружения (см. ``DATA_DIR``),
что используется тестами для изоляции от рабочей ``data/``.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .code_diagram import code_mermaid
from .diagram_gen import mermaid_has_nodes, tree_to_mermaid
from .image_gen import tree_to_image

BASE_DIR = Path(__file__).resolve().parent
# Общий каталог данных (проекты, файлы, экспорты). Переопределяется переменной
# TEAM_SERVER_DATA — удобно для развёртывания и для тестов в изоляции.
DATA_DIR = Path(os.environ.get("TEAM_SERVER_DATA") or BASE_DIR.parent / "data")
UPLOAD_DIR = DATA_DIR / "projects"
EXPORT_DIR = DATA_DIR / "exports"
# Общее хранилище с консолью Host ⇄ Pi (getting_started/protocol.py):
# файлы Pi и проекты хост складывает сюда же, поэтому веб и консоль видят
# одни и те же данные, а не две отдельные папки.
PI_FILES_DIR = DATA_DIR / "files"

# Собранный фронтенд (React SPA). Собирается в web/ (npm run build, setup.sh).
WEB_DIST = BASE_DIR.parent / "web" / "dist"

app = FastAPI(
    title="Team Server",
    description="Платформа Host ⇄ Pi: проекты, анализ кода, диаграммы и журнал.",
    docs_url=None,
    redoc_url=None,
)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")


def _bootstrap() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    PI_FILES_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    # Team Server доступен по IP в локальной сети — расширяем allowed hosts
    # для интегрированного сайта (middleware читает env при импорте).
    hosts = {h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
    hosts |= {"localhost", "127.0.0.1", "*"}
    os.environ["ALLOWED_HOSTS"] = ",".join(sorted(hosts))


@app.on_event("startup")
def startup() -> None:
    _bootstrap()


# ---------- сессия и права ----------

def current_user(request: Request) -> dict | None:
    nick = request.cookies.get("nick")
    if not nick:
        return None
    return db.get_user_by_name(nick)


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "Не авторизован"}, status_code=401)


def can_access_project(user: dict, project: dict) -> bool:
    """Доступ к материалам проекта: владелец или хост.

    Pi работает только со своими проектами и с общей витриной, а скачивать
    чужие данные (digest/PNG/API) может только Host — он сервер этой сети.
    """
    return user["role"] == "host" or project["owner_id"] == user["id"]


def _user_json(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "created_at": user.get("created_at"),
        "project_count": user.get("project_count"),
    }


# ---------- интеграция служебных разделов ----------

# Интегрированный сайт gitingest: /site/* (router и Mount — см. site_router).
from .site_router import mount_site  # noqa: E402

mount_site(app)

# HTTP-API библиотеки команд cryless (CLI на машине Pi: create/load/connection).
from .cryless_api import router as cryless_router  # noqa: E402

app.include_router(cryless_router)


# =====================================================================
#  Сессия
# =====================================================================

class NickPayload(BaseModel):
    nick: str


class RolePayload(BaseModel):
    role: str


@app.post("/api/auth/login")
def api_login(payload: NickPayload):
    """Вход без пароля: если ника нет — аккаунт создаётся (роль Pi)."""
    nick = payload.nick.strip()
    if not USERNAME_RE.match(nick):
        return JSONResponse(
            {"detail": "Ник: 2–32 символа, латиница, цифры, _ . -"}, status_code=422
        )
    existing = db.get_user_by_name(nick)
    if existing:
        db.log_action(nick, "login", "вход в веб-панель", "")
    else:
        db.create_user(nick, role="pi")
        db.log_action(nick, "register", "регистрация в веб-панели", "")
    user = db.get_user_by_name(nick)
    resp = JSONResponse({"user": _user_json(user)})
    resp.set_cookie("nick", nick, httponly=True, samesite="lax")
    return resp


@app.get("/api/auth/me")
def api_me(request: Request):
    user = current_user(request)
    if not user:
        return _unauthorized()
    return {"user": _user_json(user)}


@app.post("/api/auth/logout")
def api_logout(request: Request):
    user = current_user(request)
    if user:
        db.log_action(user["username"], "logout", "выход из веб-панели", "")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("nick")
    return resp


@app.post("/api/auth/role")
def api_role(payload: RolePayload, request: Request):
    user = current_user(request)
    if not user:
        return _unauthorized()
    if payload.role not in ("host", "pi"):
        return JSONResponse({"detail": "Недопустимая роль"}, status_code=422)
    db.set_role(user["id"], payload.role)
    db.log_action(user["username"], "role", f"выбран режим {payload.role}", "")
    return {"user": _user_json({**user, "role": payload.role})}


# =====================================================================
#  Справочники и статистика
# =====================================================================

@app.get("/api/users")
def api_users(request: Request):
    user = current_user(request)
    if not user:
        return _unauthorized()
    return {"users": [_user_json(u) for u in db.list_users()]}


@app.get("/api/stats")
def api_stats(request: Request):
    user = current_user(request)
    if not user:
        return _unauthorized()
    scope = None if user["role"] == "host" else user["username"]
    return {
        "projects": len(db.list_projects()),
        "my_projects": len(db.list_projects(owner_id=user["id"])),
        "users": len(db.list_users()),
        "actions": db.count_actions(user=scope),
        "actions_today": db.count_actions(user=scope, today=True),
        "role": user["role"],
    }


@app.get("/api/journal")
def api_journal(request: Request, limit: int = 200):
    user = current_user(request)
    if not user:
        return _unauthorized()
    limit = max(1, min(limit, 1000))
    scope = None if user["role"] == "host" else user["username"]
    return {"rows": db.list_actions(user=scope, limit=limit)}


# =====================================================================
#  Проекты
# =====================================================================

def _analysis_path(project_id: int) -> Path:
    return EXPORT_DIR / f"project_{project_id}_analysis.json"


def _project_json(user: dict, project: dict) -> dict:
    """Представление проекта для API: имя, владелец, флаги результатов."""
    pid = project["id"]
    return {
        "id": pid,
        "name": project["name"],
        "owner": project.get("owner", ""),
        "owner_id": project["owner_id"],
        "added_at": project["added_at"],
        "mine": project["owner_id"] == user["id"],
        "access": can_access_project(user, project),
        "analyzed": _analysis_path(pid).exists(),
        "has_png": (EXPORT_DIR / f"project_{pid}_tree.png").exists(),
        "has_digest": (EXPORT_DIR / f"project_{pid}_digest.txt").exists(),
        "has_diagram": (EXPORT_DIR / f"project_{pid}_diagram.mmd").exists(),
    }


def _load_analysis(project_id: int) -> dict | None:
    """Читает сохранённый результат анализа (summary/tree + метаданные диаграммы)."""
    meta_path = _analysis_path(project_id)
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    diagram_code = None
    diagram_file = EXPORT_DIR / f"project_{project_id}_diagram.mmd"
    if diagram_file.exists():
        diagram_code = diagram_file.read_text(encoding="utf-8")
    return {
        "summary": meta.get("summary", ""),
        "tree": meta.get("tree", ""),
        "diagram_code": diagram_code,
        "diagram_kind": meta.get("diagram_kind", "tree"),
        "diagram_note": meta.get("diagram_note", ""),
        "png_name": meta.get("png_name"),
    }


@app.get("/api/projects")
def api_projects(request: Request):
    user = current_user(request)
    if not user:
        return _unauthorized()
    return {"projects": [_project_json(user, p) for p in db.list_projects()]}


@app.post("/api/projects")
async def api_create_project(
    request: Request,
    name: str = Form(...),
    source: str = Form(""),          # URL репозитория (git/http) — клон при анализе
    archive: UploadFile | None = File(None),
):
    """Pi отправляет проект: zip-архив с папкой ИЛИ ссылка на репозиторий."""
    user = current_user(request)
    if not user:
        return _unauthorized()

    name = name.strip()
    if not name:
        return JSONResponse({"detail": "Укажите название проекта"}, status_code=422)

    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    dest = UPLOAD_DIR / f"{user['username']}__{slug}"
    dest.mkdir(parents=True, exist_ok=True)

    if archive is not None and archive.filename:
        data = await archive.read()
        if not data:
            return JSONResponse({"detail": "Пустой файл"}, status_code=422)
        if archive.filename.lower().endswith(".zip"):
            try:
                _safe_extract_zip(data, dest)
            except (zipfile.BadZipFile, ValueError) as e:
                return JSONResponse({"detail": f"Ошибка архива: {e}"}, status_code=422)
        else:
            (dest / archive.filename).write_bytes(data)
    elif source.strip():
        (dest / ".source_url").write_text(source.strip(), encoding="utf-8")
    else:
        return JSONResponse(
            {"detail": "Загрузите zip-архив проекта или укажите ссылку на репозиторий"},
            status_code=422,
        )

    project = db.add_project(user["id"], name, str(dest))
    db.log_action(user["username"], "upload", f"проект «{name}»", name)
    return {"project": _project_json(user, db.get_project(project["id"]))}


@app.get("/api/projects/{project_id}")
def api_project(project_id: int, request: Request):
    user = current_user(request)
    if not user:
        return _unauthorized()
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    analysis = _load_analysis(project_id) if can_access_project(user, project) else None
    return {"project": _project_json(user, project), "analysis": analysis}


@app.delete("/api/projects/{project_id}")
def api_delete_project(project_id: int, request: Request):
    user = current_user(request)
    if not user:
        return _unauthorized()
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    if not (project["owner_id"] == user["id"] or user["role"] == "host"):
        return JSONResponse({"detail": "Недостаточно прав"}, status_code=403)
    _remove_project_files(project)
    db.delete_project(project_id)
    db.log_action(user["username"], "delete", f"проект «{project['name']}»", project["name"])
    return {"ok": True}


def _remove_project_files(project: dict) -> None:
    """Удаляет файлы проекта: результаты анализа и исходную папку (если наша)."""
    pid = project["id"]
    for suffix in ("tree.png", "digest.txt", "diagram.mmd", "analysis.json"):
        (EXPORT_DIR / f"project_{pid}_{suffix}").unlink(missing_ok=True)
    src = Path(project["source"])
    try:
        # Удаляем исходник только внутри нашего хранилища — на всякий случай.
        if DATA_DIR.resolve() in src.resolve().parents:
            if src.is_dir():
                shutil.rmtree(src, ignore_errors=True)
            elif src.exists():
                src.unlink()
    except OSError:
        pass


@app.post("/api/projects/{project_id}/analyze")
def api_analyze(project_id: int, request: Request):
    """gitingest анализирует проект: структура, digest, диаграмма кода, PNG.

    Диаграмма кода строится через gitdiagram-static (тот же модуль, что в
    консоли Host ⇄ Pi); если код не поддаётся анализу — диаграмма по дереву.
    """
    user = current_user(request)
    if not user:
        return _unauthorized()
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    if not can_access_project(user, project):
        return JSONResponse(
            {"detail": "Недостаточно прав: проект принадлежит другому пользователю"},
            status_code=403,
        )

    try:
        result = analyze_and_export(project)
    except SourceError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    except AnalysisError as e:
        return JSONResponse({"detail": str(e)}, status_code=500)

    db.log_action(
        user["username"], "analyze", f"проект «{project['name']}»", project["name"]
    )
    return result


@app.get("/api/projects/{project_id}/diagram")
def api_project_diagram(project_id: int, request: Request):
    user = current_user(request)
    if not user:
        return _unauthorized()
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    if not can_access_project(user, project):
        return JSONResponse({"detail": "Недостаточно прав (нужен режим Host)"}, status_code=403)
    diagram_file = EXPORT_DIR / f"project_{project_id}_diagram.mmd"
    if not diagram_file.exists():
        return JSONResponse({"detail": "Сначала проанализируйте проект"}, status_code=409)
    meta = _load_analysis(project_id) or {}
    db.log_action(user["username"], "diagram", "просмотр диаграммы", project["name"])
    return {
        "code": diagram_file.read_text(encoding="utf-8"),
        "kind": meta.get("diagram_kind", "tree"),
        "note": meta.get("diagram_note", ""),
    }


@app.get("/api/projects/{project_id}/digest")
def api_project_digest(project_id: int, request: Request):
    """Полный текстовый дамп проекта (digest) — как в gitingest."""
    user = current_user(request)
    if not user:
        return _unauthorized()
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    if not can_access_project(user, project):
        return JSONResponse({"detail": "Недостаточно прав (нужен режим Host)"}, status_code=403)
    digest = EXPORT_DIR / f"project_{project_id}_digest.txt"
    if not digest.exists():
        return JSONResponse({"detail": "Сначала проанализируйте проект"}, status_code=409)
    db.log_action(user["username"], "download", "digest", project["name"])
    return Response(
        content=digest.read_text(encoding="utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{project["name"]}_digest.txt"'},
    )


@app.get("/api/projects/{project_id}/png")
def api_project_png(project_id: int, request: Request):
    """PNG со структурой проекта (генерируется при анализе)."""
    user = current_user(request)
    if not user:
        return _unauthorized()
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    if not can_access_project(user, project):
        return JSONResponse({"detail": "Недостаточно прав (нужен режим Host)"}, status_code=403)
    export = EXPORT_DIR / f"project_{project_id}_tree.png"
    if not export.exists():
        return JSONResponse({"detail": "Сначала проанализируйте проект"}, status_code=409)
    db.log_action(user["username"], "png", "PNG структуры", project["name"])
    return Response(
        content=export.read_bytes(),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{project["name"]}_tree.png"'},
    )


@app.get("/api/projects/{project_id}/export")
def api_project_export(project_id: int, request: Request):
    """Выгрузка данных проекта в JSON (digest + метаданные)."""
    user = current_user(request)
    if not user:
        return _unauthorized()
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    if not can_access_project(user, project):
        return JSONResponse({"detail": "Недостаточно прав (нужен режим Host)"}, status_code=403)
    digest = EXPORT_DIR / f"project_{project_id}_digest.txt"
    content = digest.read_text(encoding="utf-8") if digest.exists() else None
    analysis = _load_analysis(project_id)
    db.log_action(user["username"], "api_export", "выгрузка JSON", project["name"])
    return {
        "id": project["id"],
        "name": project["name"],
        "owner": project["owner"],
        "added_at": project["added_at"],
        "analyzed": content is not None,
        "summary": (analysis or {}).get("summary"),
        "tree": (analysis or {}).get("tree"),
        "diagram_kind": (analysis or {}).get("diagram_kind"),
        "digest": content,
    }


# =====================================================================
#  Произвольный репозиторий (gitingest) — раздел «Анализ репозитория»
# =====================================================================

class IngestPayload(BaseModel):
    url: str
    max_size: int = 100
    token: str = ""


@app.post("/api/ingest")
async def api_ingest(payload: IngestPayload, request: Request):
    """Разбирает любой git-репозиторий по ссылке (не сохраняется как проект)."""
    user = current_user(request)
    if not user:
        return _unauthorized()
    url = payload.url.strip()
    if not url:
        return JSONResponse({"detail": "Укажите ссылку на репозиторий"}, status_code=422)
    max_size = max(1, min(payload.max_size, 500))

    from server.models import PatternType
    from server.query_processor import process_query

    try:
        res = await process_query(
            input_text=url,
            max_file_size=max_size,
            pattern_type=PatternType.EXCLUDE,
            pattern="",
            token=payload.token.strip() or None,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"detail": f"Ошибка анализа: {exc!s}"}, status_code=500)

    if not hasattr(res, "summary"):
        return JSONResponse(
            {"detail": getattr(res, "error", "Не удалось разобрать репозиторий")},
            status_code=422,
        )
    db.log_action(user["username"], "ingest", f"репозиторий {url}", "")
    return {
        "url": getattr(res, "repo_url", url),
        "summary": res.summary,
        "tree": res.tree,
        "digest": res.content,
    }


# =====================================================================
#  Анализ проекта (общий код веб-API и консоли Host ⇄ Pi)
# =====================================================================

def _safe_extract_zip(data: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            if member.startswith("/") or ".." in Path(member).parts:
                raise ValueError(f"Недопустимый путь в архиве: {member}")
        zf.extractall(dest)


def _run_gitingest(path_or_url: str) -> tuple[str, str, str]:
    from gitingest import ingest

    return ingest(path_or_url)


def _resolve_project_target(project: dict) -> Path:
    """Приводит источник проекта к каталогу, пригодному для анализа.

    Проекты попадают в систему двумя путями:
      * веб-панель — распакованная папка (data/projects/<user>__<slug>);
      * консоль Host ⇄ Pi — папка ``data/projects/<name>`` либо ``<name>.zip``
        (консоль распаковывает архив рядом, см. getting_started/core.py).

    Если источнику соответствует архив, распаковываем его в соседнюю папку.
    Для одиночного файла анализ невозможен — поднимаем ValueError.
    """
    src = Path(project["source"])
    if src.is_dir():
        return src
    if src.is_file() and src.suffix.lower() == ".zip":
        unpacked = src.with_suffix("")
        if not unpacked.is_dir():
            with zipfile.ZipFile(src) as zf:
                _safe_extract_zip(src.read_bytes(), unpacked)
        return unpacked
    if src.is_file():
        raise ValueError(
            "Проект сохранён как одиночный файл — анализ возможен только для "
            "папки или zip-архива проекта"
        )
    raise ValueError(f"Источник проекта не найден на сервере: {project['source']}")


class SourceError(Exception):
    """Источник проекта не пригоден для анализа (клиентская ошибка, HTTP 422)."""


class AnalysisError(Exception):
    """Анализ не удался по вине сервера (HTTP 500)."""


def analyze_and_export(project: dict) -> dict:
    """Анализирует проект и сохраняет результаты в EXPORT_DIR.

    Общий код веб-API и HTTP-API cryless (``cryless load data`` анализирует
    загруженные данные на сервере), поэтому и сайт, и консоль Pi видят одни и
    те же диаграммы.

    Возвращает словарь для JSON-ответа; поднимает SourceError (422) для
    неподходящего источника и AnalysisError (500), если анализ упал.
    """
    project_id = project["id"]
    src_path = Path(project["source"])
    url_file = src_path / ".source_url"
    has_files = src_path.is_dir() and any(
        p for p in src_path.iterdir() if p.name != ".source_url"
    )
    try:
        if not has_files and url_file.exists():
            # проект добавлен ссылкой на репозиторий — клонируем при анализе
            target: str | Path = url_file.read_text(encoding="utf-8").strip()
        else:
            target = _resolve_project_target(project)
        if isinstance(target, Path):
            target = str(target)
    except (OSError, ValueError, zipfile.BadZipFile, NotADirectoryError) as e:
        raise SourceError(str(e)) from e

    try:
        summary, tree, content = _run_gitingest(target)
    except Exception as e:  # noqa: BLE001
        raise AnalysisError(f"Ошибка анализа: {e}") from e

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    png = tree_to_image(
        tree,
        f"{project['owner']}/{project['name']}",
        summary.splitlines()[0] if summary else "",
    )
    export = EXPORT_DIR / f"project_{project_id}_tree.png"
    export.write_bytes(png)
    digest = EXPORT_DIR / f"project_{project_id}_digest.txt"
    digest.write_text(content, encoding="utf-8")

    # gitdiagram-стиль: диаграмма по дереву проекта (рендер в браузере)
    tree_body = "\n".join(
        ln for ln in tree.splitlines() if ln.strip() and not ln.startswith("Directory structure")
    )
    tree_diagram = tree_to_mermaid(tree_body or tree)

    # Диаграмма кода (gitdiagram-static). Если анализ не дал узлов — fallback
    # на диаграмму по дереву, чтобы Pi всегда видел содержательную картинку.
    code_diagram, code_error = (None, "")
    if isinstance(target, str) and Path(target).is_dir():
        code_diagram, code_error = code_mermaid(target)
    if code_diagram and not mermaid_has_nodes(code_diagram):
        code_error = code_error or "статический анализ не нашёл связей между модулями"
        code_diagram = None
    diagram_code = code_diagram or tree_diagram
    (EXPORT_DIR / f"project_{project_id}_diagram.mmd").write_text(
        diagram_code, encoding="utf-8"
    )

    note = code_error if code_diagram is None and code_error else ""
    result = {
        "summary": summary,
        "tree": tree,
        "diagram_code": diagram_code,
        "diagram_kind": "code" if code_diagram else "tree",
        "diagram_note": note,
        "png_name": export.name,
    }
    # Метаданные для повторной отдачи без нового анализа (GET /api/projects/{id}).
    _analysis_path(project_id).write_text(
        json.dumps(
            {
                "summary": summary,
                "tree": tree,
                "diagram_kind": result["diagram_kind"],
                "diagram_note": note,
                "png_name": export.name,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return result


# =====================================================================
#  Основной сайт: раздача собранного React-приложения (SPA)
# =====================================================================

if (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    """Любой «человеческий» адрес отдаёт SPA; статика — файлами из dist."""
    if full_path.startswith(("api/", "site/", "static/")):
        return JSONResponse({"detail": "Не найдено"}, status_code=404)
    web_root = WEB_DIST.resolve()
    candidate = (WEB_DIST / full_path).resolve()
    if full_path and candidate.is_file() and web_root in candidate.parents:
        return FileResponse(candidate)
    index = WEB_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(
        {
            "detail": (
                "Фронтенд не собран. Выполните ./setup.sh "
                "(или «cd web && npm install && npm run build»)."
            )
        },
        status_code=503,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
