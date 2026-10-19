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
from . import qwen
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


def _client_ip(request: Request) -> str:
    """IP текущего входа.

    За обратным прокси на той же машине (nginx и т.п.) — X-Forwarded-For,
    иначе прямой адрес клиента.
    """
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff
    return request.client.host if request.client else ""


def _last_login_ip_path() -> Path:
    """Файл с IP последнего входа: data/last_login_ip (в git не тащится)."""
    return UPLOAD_DIR.parent / "last_login_ip"


def _last_login_ip() -> str:
    """IP последнего входа; "" — входа ещё не было."""
    try:
        return _last_login_ip_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _save_login_ip(ip: str) -> None:
    try:
        path = _last_login_ip_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ip, encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass  # не записался — следующий вход посчитается первым (без стирания)


@app.post("/api/auth/login")
async def api_login(payload: NickPayload, request: Request):
    """Вход без пароля: если ника нет — аккаунт создаётся (роль Pi).

    С нового IP данные прошлого входа забываются: профиль браузера (куки,
    история, сохранённые пароли) стирается — см. qwen.forget_profile.
    Вход с того же IP ничего не трогает.
    """
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

    ip = _client_ip(request)
    last_ip = _last_login_ip() if ip else ""
    forgotten = False
    if ip and last_ip and last_ip != ip:
        # новый IP: следов прошлого входа не оставляем — профиль стирается
        forgotten = True
        try:
            await qwen.forget_profile()
            db.log_action(
                nick, "login", f"вход с нового IP {ip} — профиль браузера стёр", ""
            )
        except qwen.QwenError as exc:
            db.log_action(
                nick,
                "login",
                f"вход с нового IP {ip} — профиль стёрть не вышло: {exc}",
                "",
            )
    if ip:
        _save_login_ip(ip)

    resp = JSONResponse({"user": _user_json(user), "profile_forgotten": forgotten})
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
#  Файлы проекта: браузер кода и «пульс» (вкладки «Код» и «Пульс»)
# =====================================================================

#: Служебные каталоги, которые не показываем в браузере файлов.
_BROWSE_SKIP = {
    ".git", "__pycache__", ".pytest_cache", "node_modules",
    ".mypy_cache", ".ruff_cache", ".venv", "venv", ".idea", ".vscode",
}
#: Сколько байт файла отдаём для просмотра в браузере.
MAX_VIEW_BYTES = 512 * 1024


def _browse_root(project: dict) -> Path:
    """Корень файлов проекта для браузера кода (папка или распакованный zip)."""
    src = Path(project["source"])
    if src.is_dir():
        return src.resolve()
    if src.is_file() and src.suffix.lower() == ".zip":
        unpacked = src.with_suffix("")
        if not unpacked.is_dir():
            with zipfile.ZipFile(src) as zf:
                _safe_extract_zip(src.read_bytes(), unpacked)
        return unpacked.resolve()
    raise ValueError("Файлы проекта недоступны: источник не найден на сервере")


def _safe_child(root: Path, rel: str) -> Path:
    """Разрешает ``rel`` внутри ``root``, отсекая ``../`` и абсолютные пути."""
    rel = (rel or "").strip().lstrip("/")
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Недопустимый путь")
    return target


def _require_project(project_id: int, request: Request):
    """Общий пролог: сессия + проект + доступ. Возвращает (user, project) либо JSONResponse."""
    user = current_user(request)
    if not user:
        return _unauthorized()
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    if not can_access_project(user, project):
        return JSONResponse(
            {"detail": "Недостаточно прав: материалы проекта доступны владельцу и Host"},
            status_code=403,
        )
    return user, project


#: Магические байты → человекочитаемый тип файла (браузер кода показывает их
#: вместо пустого «двоичный файл»: исполняемые файлы, картинки, архивы…).
_BINARY_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x7fELF", "ELF — исполняемый файл/библиотека (Linux)"),
    (b"MZ", "PE — исполняемый файл (Windows)"),
    (b"\x89PNG\r\n\x1a\n", "PNG — изображение"),
    (b"\xff\xd8\xff", "JPEG — изображение"),
    (b"GIF87a", "GIF — изображение"),
    (b"GIF89a", "GIF — изображение"),
    (b"BM", "BMP — изображение"),
    (b"%PDF", "PDF — документ"),
    (b"PK\x03\x04", "ZIP — архив (или docx/xlsx/epub)"),
    (b"\x1f\x8b", "gzip — архив"),
    (b"Rar!\x1a\x07", "RAR — архив"),
    (b"7z\xbc\xaf\x27\x1c", "7z — архив"),
    (b"\x00asm", "WebAssembly — модуль"),
    (b"SQLite format 3\x00", "SQLite — база данных"),
    (b"\xca\xfe\xba\xbe", "Java class"),
    (b"\x00\x00\x01\x00", "ICO — иконка"),
    (b"RIFF", "RIFF — медиафайл (wav/webp/avi)"),
)


def _binary_info(data: bytes) -> dict:
    """Что за бинарник: тип по магии, hex-дамп и строки, вшитые в файл.

    В текстовом файле «написанный код» и так виден, а у исполняемого файла
    исходников нет — вместо пустого сообщения показываем hex-дамп первых
    байт и печатные строки (как утилита ``strings``): в них часто видно,
    из чего и как собран файл.
    """
    kind = "двоичный файл"
    for magic, name in _BINARY_MAGIC:
        if data.startswith(magic):
            kind = name
            if magic == b"\x7fELF" and len(data) > 4:
                kind += ", 64-битный" if data[4] == 2 else ", 32-битный"
            break

    dump = data[:512]
    lines = []
    for off in range(0, len(dump), 16):
        chunk = dump[off : off + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hex_part:<47}  {ascii_part}")

    strings: list[str] = []
    for match in re.finditer(rb"[\x20-\x7e]{5,}", data):
        if len(strings) >= 200:
            break
        strings.append(match.group().decode("ascii")[:120])

    return {"kind": kind, "hex": "\n".join(lines), "strings": strings}


def _safe_filename(name: str) -> str:
    """Имя файла для Content-Disposition: без кавычек, «../» и управляющих символов."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .")
    return name or "file"


@app.get("/api/projects/{project_id}/files")
def api_project_files(project_id: int, path: str = "", request: Request = None):
    """Список файлов и папок проекта (браузер кода, как на GitHub)."""
    ctx = _require_project(project_id, request)
    if isinstance(ctx, JSONResponse):
        return ctx
    _user, project = ctx
    try:
        root = _browse_root(project)
        target = _safe_child(root, path)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    if not target.is_dir():
        return JSONResponse({"detail": "Это не папка"}, status_code=400)

    entries: list[dict] = []
    try:
        children = sorted(
            target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        )
    except OSError as e:
        return JSONResponse({"detail": f"Не удалось прочитать папку: {e}"}, status_code=500)
    for child in children:
        if child.name in _BROWSE_SKIP:
            continue
        is_dir = child.is_dir()
        try:
            size = 0 if is_dir else child.stat().st_size
        except OSError:
            size = 0
        entries.append(
            {
                "name": child.name,
                "path": child.relative_to(root).as_posix(),
                "type": "dir" if is_dir else "file",
                "size": size,
            }
        )
    rel_path = "" if target == root else target.relative_to(root).as_posix()
    return {"path": rel_path, "entries": entries}


@app.get("/api/projects/{project_id}/file")
def api_project_file(project_id: int, path: str, request: Request):
    """Содержимое одного файла проекта (для просмотра в браузере кода)."""
    ctx = _require_project(project_id, request)
    if isinstance(ctx, JSONResponse):
        return ctx
    _user, project = ctx
    try:
        root = _browse_root(project)
        target = _safe_child(root, path)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    if not target.is_file():
        return JSONResponse({"detail": "Файл не найден"}, status_code=404)

    size = target.stat().st_size
    try:
        data = target.read_bytes()[:MAX_VIEW_BYTES]
    except OSError as e:
        return JSONResponse({"detail": f"Не удалось прочитать файл: {e}"}, status_code=500)
    truncated = size > MAX_VIEW_BYTES
    binary = b"\x00" in data
    content = "" if binary else data.decode("utf-8", errors="replace")
    result = {
        "path": target.relative_to(root).as_posix(),
        "size": size,
        "binary": binary,
        "truncated": truncated,
        "content": content,
    }
    if binary:
        # для бинарника вместо пустого сообщения — тип, hex-дамп и строки
        result.update(_binary_info(data))
    return result


@app.get("/api/projects/{project_id}/source")
def api_project_source(project_id: int, request: Request):
    """Исходники проекта целиком — zip-архив (кнопка «Исходник» рядом с Digest)."""
    ctx = _require_project(project_id, request)
    if isinstance(ctx, JSONResponse):
        return ctx
    _user, project = ctx
    try:
        root = _browse_root(project)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        return JSONResponse({"detail": str(e)}, status_code=404)

    buf = io.BytesIO()
    files_count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # обходим вручную: служебные каталоги (node_modules и т.п.) не заходим
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                children = sorted(current.iterdir())
            except OSError:
                continue
            for child in children:
                if child.name in _BROWSE_SKIP or child.is_symlink():
                    continue
                if child.is_dir():
                    stack.append(child)
                elif child.is_file():
                    zf.write(child, child.relative_to(root).as_posix())
                    files_count += 1
    if files_count == 0:
        return JSONResponse({"detail": "В проекте нет файлов"}, status_code=404)

    db.log_action(_user["username"], "download", "исходник проекта", project["name"])
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_filename(project["name"])}.zip"'
        },
    )


@app.get("/api/projects/{project_id}/download")
def api_project_download(project_id: int, path: str, request: Request):
    """Скачивание одного файла проекта (кнопка «Скачать» в просмотре файла)."""
    ctx = _require_project(project_id, request)
    if isinstance(ctx, JSONResponse):
        return ctx
    _user, project = ctx
    try:
        root = _browse_root(project)
        target = _safe_child(root, path)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    if not target.is_file():
        return JSONResponse({"detail": "Файл не найден"}, status_code=404)
    try:
        data = target.read_bytes()
    except OSError as e:
        return JSONResponse({"detail": f"Не удалось прочитать файл: {e}"}, status_code=500)

    db.log_action(_user["username"], "download", f"файл {target.name}", project["name"])
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{_safe_filename(target.name)}"'},
    )


@app.get("/api/projects/{project_id}/pulse")
def api_project_pulse(project_id: int, request: Request):
    """«Пульс» проекта: активность из журнала + статистика файлов (вкладка Insights)."""
    ctx = _require_project(project_id, request)
    if isinstance(ctx, JSONResponse):
        return ctx
    _user, project = ctx

    actions = [a for a in db.list_actions(limit=1000) if a.get("project") == project["name"]]

    files = dirs = total_bytes = 0
    try:
        root = _browse_root(project)
        for p in root.rglob("*"):
            if any(part in _BROWSE_SKIP for part in p.parts):
                continue
            if p.is_dir():
                dirs += 1
            elif p.is_file():
                files += 1
                try:
                    total_bytes += p.stat().st_size
                except OSError:
                    pass
    except (OSError, ValueError, zipfile.BadZipFile):
        pass

    return {
        "actions": actions,
        "files": files,
        "dirs": dirs,
        "bytes": total_bytes,
        "analyzed": _analysis_path(project_id).exists(),
    }


# =====================================================================
#  «Холст» проекта — рисунок Excalidraw, живущий только в системе
# =====================================================================

# Холст — это не файл репозитория, а сущность платформы: он хранится в БД
# и никогда не попадает в git. Лимит щедрый, потому что в сцену Excalidraw
# могут быть встроены картинки (base64), но всё же ограничен.
MAX_CANVAS_CHARS = 5_000_000


class CanvasPayload(BaseModel):
    scene: str  # JSON-сцена Excalidraw: {type, version, elements, appState, files}


def _canvas_json(scene: str, updated_at: str | None, updated_by: str | None) -> dict:
    return {"scene": scene, "updated_at": updated_at, "updated_by": updated_by}


_EMPTY_SCENE = json.dumps({"type": "excalidraw", "version": 2, "elements": [], "appState": {}, "files": {}})


@app.get("/api/projects/{project_id}/canvas")
def api_project_canvas(project_id: int, request: Request):
    """Холст проекта: сцена Excalidraw. Пустая сцена — если холст не заводили."""
    ctx = _require_project(project_id, request)
    if isinstance(ctx, JSONResponse):
        return ctx
    row = db.get_project_canvas(project_id)
    if not row:
        return _canvas_json(_EMPTY_SCENE, None, None)
    return _canvas_json(row["scene"], row["updated_at"], row["updated_by"])


@app.put("/api/projects/{project_id}/canvas")
def api_project_canvas_save(project_id: int, payload: CanvasPayload, request: Request):
    """Сохраняет холст проекта (владелец или Host)."""
    ctx = _require_project(project_id, request)
    if isinstance(ctx, JSONResponse):
        return ctx
    user, project = ctx
    scene = payload.scene
    if len(scene) > MAX_CANVAS_CHARS:
        return JSONResponse(
            {"detail": f"Холст слишком большой (максимум {MAX_CANVAS_CHARS} символов)"},
            status_code=413,
        )
    # Храним только валидную сцену — иначе фронтенд при загрузке упадёт.
    try:
        parsed = json.loads(scene)
    except (TypeError, ValueError):
        return JSONResponse({"detail": "Холст должен содержать JSON-сцену"}, status_code=422)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("elements", []), list):
        return JSONResponse(
            {"detail": "Некорректная сцена: нужен объект с полем elements (список)"},
            status_code=422,
        )

    # Повторная запись той же сцены (фронтенд шлёт её при каждом открытии
    # вкладки) — no-op: не двигаем время правки и не пишем дубль в журнал.
    existing = db.get_project_canvas(project_id)
    if existing and existing["scene"] == scene:
        return _canvas_json(existing["scene"], existing["updated_at"], existing["updated_by"])

    row = db.save_project_canvas(project_id, scene, user["id"])
    db.log_action(user["username"], "canvas", "сохранил холст", project["name"])
    return _canvas_json(row["scene"], row["updated_at"], row["updated_by"])


# =====================================================================
#  «Песочница»: разговор с ИИ (Qwen) через браузер на сервере
# =====================================================================
#
# Здесь нет собственного чат-бэкенда: парсер (app_platform/qwen.py) открывает
# chat.qwen.ai в отдельном окне с постоянным профилем. Пользователь входит в
# аккаунт один раз вручную, а вопрос/ответ ходят через наш API.

#: Сколько символов вопроса допускаем (дальше — перебор, а не вопрос).
MAX_PROMPT_CHARS = 20_000
#: Максимум содержимого, который пишем в файл проекта по кнопке «Применить».
MAX_FILE_EDIT_CHARS = 400_000


class SandboxAskPayload(BaseModel):
    prompt: str
    project_id: int | None = None  # если указан — файлы проекта уходят как контекст
    agent: bool = False  # режим агента: ИИ сам правит файлы проекта (без «Применить»)


class SandboxApplyPayload(BaseModel):
    project_id: int
    path: str  # относительный путь внутри проекта
    content: str  # новое содержимое файла целиком


class SandboxModePayload(BaseModel):
    agent: bool = False  # включить/выключить родный «Agent Mode» на сайте Qwen


async def _qwen_status_or_error():
    """Общий код для статуса/открытия: либо (dict, None), либо (None, JSONResponse)."""
    try:
        return await qwen.status(), None
    except qwen.QwenError as exc:
        return None, JSONResponse({"detail": str(exc)}, status_code=503)


@app.get("/api/sandbox/status")
async def api_sandbox_status(request: Request):
    """Открыт ли браузер Qwen, вошли ли в аккаунт."""
    if not current_user(request):
        return _unauthorized()
    status, err = await _qwen_status_or_error()
    return err if err else status


@app.post("/api/sandbox/browser")
async def api_sandbox_browser(request: Request):
    """Открывает (или пробуждает) окно браузера с чатом Qwen на сервере."""
    user = current_user(request)
    if not user:
        return _unauthorized()
    try:
        status = await qwen.open_browser()
    except qwen.QwenError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=503)
    db.log_action(user["username"], "sandbox", "открыл браузер ИИ")
    return status


@app.post("/api/sandbox/reset")
async def api_sandbox_reset(request: Request):
    """Начинает новый диалог: страница Qwen сбрасывается в пустой чат."""
    user = current_user(request)
    if not user:
        return _unauthorized()
    try:
        status = await qwen.new_chat()
    except qwen.QwenError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=503)
    db.log_action(user["username"], "sandbox", "начал новый диалог с ИИ")
    return status


@app.post("/api/sandbox/mode")
async def api_sandbox_mode(payload: SandboxModePayload, request: Request):
    """Щёлкает родным «Agent Mode» на странице Qwen в окне браузера сервера.

    Переключатель «Режим агента» в «Песочнице» включает и наш протокол
    инструментов, и режим на самом сайте Qwen — окно браузера показывает
    включённый пункт меню режимов.
    """
    user = current_user(request)
    if not user:
        return _unauthorized()
    try:
        on = await qwen.set_site_agent_mode(payload.agent)
    except qwen.QwenError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=503)
    db.log_action(
        user["username"],
        "sandbox",
        "включил режим агента на сайте Qwen" if on else "выключил режим агента на сайте Qwen",
    )
    return {"agent": on}


def _ai_create_repo(user: dict, name: str) -> dict:
    """Создаёт новый проект (репозиторий) по просьбе ИИ. Возвращает проект.

    Каталог и запись в БД — как при обычной загрузке (``api_create_project``),
    только без архива: файлы в новый репозиторий дописывает сам ИИ
    (инструмент «запись») или пользователь через браузер кода. Повторное
    создание с тем же именем у того же владельца — ошибка (ИИ получит её
    результатом инструмента и попробует другое имя).
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("не указано название репозитория")
    if len(name) > 64:
        raise ValueError("название слишком длинное (максимум 64 символа)")
    if "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError("название не должно содержать «/», «\\» или «..»")
    if any(p["name"] == name for p in db.list_projects(user["id"])):
        raise ValueError(f"репозиторий «{name}» уже существует")

    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    dest = UPLOAD_DIR / f"{user['username']}__{slug}"
    guard = 0
    while dest.exists():  # слаги разных имён могли совпасть — добиваем суффиксом
        guard += 1
        if guard > 20:
            raise ValueError("не удалось подобрать каталог для репозитория")
        dest = UPLOAD_DIR / f"{user['username']}__{slug}_{guard}"
    dest.mkdir(parents=True, exist_ok=True)

    project = db.add_project(user["id"], name, str(dest))
    db.log_action(user["username"], "sandbox", f"ИИ создал репозиторий «{name}»", name)
    return project


@app.post("/api/sandbox/ask")
async def api_sandbox_ask(payload: SandboxAskPayload, request: Request):
    """Задаёт вопрос ИИ и возвращает готовый ответ.

    Промпт уходит в настоящий браузер Qwen на сервере; если указан проект —
    к вопросу подкладывается содержимое его файлов, чтобы ИИ видел код.
    Блок «ИНСТРУМЕНТ: создать» из ответа (и в обычном чате, и в режиме
    агента) выполняется сразу: появляется новый репозиторий (проект).
    """
    user = current_user(request)
    if not user:
        return _unauthorized()

    prompt = (payload.prompt or "").strip()
    if not prompt:
        return JSONResponse({"detail": "Введите вопрос к ИИ."}, status_code=422)
    if len(prompt) > MAX_PROMPT_CHARS:
        return JSONResponse(
            {"detail": f"Вопрос слишком длинный (максимум {MAX_PROMPT_CHARS} символов)"},
            status_code=413,
        )

    # Колбэк «создать» для режима агента: создаёт проект и отдаёт его папку,
    # чтобы агент продолжил работу уже внутри нового репозитория.
    created_list: list[dict] = []

    def _create_repo_cb(name: str) -> tuple[str, str, Path]:
        new_project = _ai_create_repo(user, name)
        created_list.append({"id": new_project["id"], "name": new_project["name"]})
        return (
            f"Репозиторий «{new_project['name']}» создан — папка проекта готова, "
            "можно писать в него файлы инструментом «запись».",
            f"создан репозиторий «{new_project['name']}»",
            _browse_root(new_project),
        )

    # Режим агента: ИИ сам применяет правки к файлам, поэтому для правки
    # нужен проект и права на запись (те же, что у /api/sandbox/apply).
    # Без проекта агент тоже работает — он создаёт репозиторий инструментом
    # «создать» и продолжает в нём же.
    if payload.agent:
        project: dict | None = None
        root = None
        if payload.project_id:
            ctx = _require_project(payload.project_id, request)
            if isinstance(ctx, JSONResponse):
                return ctx
            _user, project = ctx
            if not (project["owner"] == user["username"] or user["role"] == "host"):
                return JSONResponse(
                    {"detail": "Править файлы может только владелец проекта или Host"},
                    status_code=403,
                )
            try:
                root = _browse_root(project)
            except ValueError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)

        try:
            answer, agent_log = await qwen.run_agent(
                prompt,
                root,
                project["name"] if project else "",
                create_repo=_create_repo_cb,
            )
        except qwen.QwenError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)

        # Журнал: проект — выбранный, либо только что созданный репозиторий.
        journal_project = (
            project["name"] if project else (created_list[-1]["name"] if created_list else "")
        )
        db.log_action(user["username"], "sandbox", f"агент ИИ: {prompt[:80]}", journal_project)
        for step in agent_log:
            if step["tool"] == "запись" and not step["note"].startswith("ошибка"):
                db.log_action(
                    user["username"],
                    "sandbox",
                    f"агент ИИ записал {step['path']}",
                    journal_project,
                )
        result_body = {
            "answer": answer,
            "project": project["name"] if project else "",
            "edits": [],  # правки уже применены — ручное «Применить» не нужно
            "agent_log": agent_log,
        }
        if created_list:
            result_body["created"] = created_list[-1]
        return result_body

    # Контекст проекта: только файлы, к которым у пользователя есть доступ.
    context = ""
    project_name = ""
    if payload.project_id:
        ctx = _require_project(payload.project_id, request)
        if isinstance(ctx, JSONResponse):
            return ctx
        project = ctx[1]
        project_name = project["name"]
        try:
            context = qwen.build_project_context(_browse_root(project), project["name"])
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)

    # Инструкция «создать репозиторий» — в самый конец промпта, ПОСЛЕ файлов
    # проекта: прочитанная последней, она срабатывает надёжнее — иначе ИИ
    # отвечает обычным текстом и вместо блока выдаёт команды терминала.
    # То же самое в режиме агента делает build_project_context(rules=AGENT_RULES).
    context = (
        f"{context}\n\n{qwen.CREATE_RULES.strip()}" if context else qwen.CREATE_RULES.strip()
    )
    try:
        answer = await qwen.ask(prompt, context=context)
    except qwen.QwenError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=503)

    # Выполняем вызовы «создать» и вычищаем сырые блоки протокола из текста.
    created: dict | None = None
    create_calls = [c for c in qwen.parse_agent_calls(answer) if c["tool"] == "создать"]
    if not create_calls and re.search(r"репозитор|\bрепо\b", prompt, re.I):
        # просят репозиторий, а блока нет — подсказка тонула или модель ответила
        # командами: пишем в лог, чтобы по нему видно было, что починить
        qwen.logger.warning(
            "Блок «создать» не найден в ответе ИИ (промпт: {}…)", prompt[:80]
        )
    if create_calls:
        answer = qwen.strip_tool_calls(answer, {"создать"})
        for call in create_calls:
            try:
                new_project = _ai_create_repo(user, call.get("name") or call.get("path") or "")
            except (ValueError, OSError) as exc:
                answer = f"{answer}\n\n**Репозиторий не создан:** {exc}."
                continue
            created = {"id": new_project["id"], "name": new_project["name"]}
            answer = (
                f"{answer}\n\n✅ **Репозиторий «{created['name']}» создан** — "
                "он уже в списке проектов и подключён к чату."
            )

    db.log_action(
        user["username"], "sandbox", f"вопрос ИИ: {prompt[:80]}", project_name
    )
    result_body = {
        "answer": answer,
        "project": project_name,
        "edits": qwen.parse_file_edits(answer) if project_name else [],
    }
    if created:
        result_body["created"] = created
    return result_body


@app.post("/api/sandbox/apply")
async def api_sandbox_apply(payload: SandboxApplyPayload, request: Request):
    """Применяет правку из ответа ИИ к файлу проекта на сервере.

    ИИ помечает правки блоками «ФАЙЛ: путь» … «КОНЕЦ-ФАЙЛА» (см.
    qwen.parse_file_edits). Пользователь нажимает «Применить» — и содержимое
    файла перезаписывается на сервере (правит только владелец проекта или
    Host, действие уходит в журнал).
    """
    ctx = _require_project(payload.project_id, request)
    if isinstance(ctx, JSONResponse):
        return ctx
    user, project = ctx
    if not (project["owner"] == user["username"] or user["role"] == "host"):
        return JSONResponse(
            {"detail": "Править файлы может только владелец проекта или Host"},
            status_code=403,
        )

    try:
        root = _browse_root(project)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    content = payload.content or ""
    if len(content) > MAX_FILE_EDIT_CHARS:
        return JSONResponse(
            {"detail": f"Файл слишком большой (максимум {MAX_FILE_EDIT_CHARS} символов)"},
            status_code=413,
        )

    try:
        target = _safe_child(root, payload.path)
    except ValueError:
        return JSONResponse({"detail": "Недопустимый путь к файлу"}, status_code=400)
    if target.is_dir():
        return JSONResponse({"detail": "По этому пути папка, а не файл"}, status_code=400)
    if not target.exists():
        return JSONResponse({"detail": "Файл не найден в проекте"}, status_code=404)

    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return JSONResponse({"detail": f"Не удалось записать файл: {exc}"}, status_code=500)

    db.log_action(
        user["username"], "sandbox", f"применил правку ИИ в {payload.path}", project["name"]
    )
    return {"path": payload.path, "bytes": len(content.encode("utf-8"))}


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
