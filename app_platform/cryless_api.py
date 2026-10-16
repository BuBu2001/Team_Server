"""HTTP-API для CLI-библиотеки команд ``cryless`` (работает как git).

Здесь серверная часть клиента: Pi запускает ``cryless`` у себя в корне проекта,
а команды обращаются к этому API на Host.

    GET  /api/cryless/ping                          — «cryless connection check»
    POST /api/cryless/repositories                  — «cryless create repository»
    POST /api/cryless/repositories/{name}/data      — «cryless load data»

Авторизация простая и соответствует остальной системе (панель тоже без
паролей, это локальная сеть):
  * создание репозитория — заголовок ``X-Cryless-User`` с ником;
  * загрузка данных — токен репозитория (``Authorization: Bearer <token>``),
    который выдаётся один раз при создании и лежит в ``.cryless/config.json``.

Имена репозиториев: только латиница, цифры, ``. _ -`` — без пробелов и
кириллицы (такое имя становится именем папки проекта на сервере).
"""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import __version__, db

router = APIRouter(prefix="/api/cryless", tags=["cryless"])

SERVICE = "team-server"
#: Имя репозитория: начинается с буквы/цифры, дальше латиница, цифры, . _ -
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")
#: Предел размера загружаемого архива (данных с Pi).
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def _platform():
    """Актуальный модуль main (в тестах его каталоги подменяются)."""
    from . import main as platform_main

    return platform_main


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _human(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.1f} {unit}" if unit != "Б" else f"{int(value)} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def _count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


# ---------- проверка соединения ----------

@router.get("/ping")
def ping():
    """«cryless connection check»: сервер жив, версия и число репозиториев."""
    return {
        "ok": True,
        "service": SERVICE,
        "version": __version__,
        "time": _now(),
        "repositories": len(db.list_projects()),
    }


# ---------- создание репозитория ----------

class RepositoryCreate(BaseModel):
    name: str
    force: bool = False


@router.post("/repositories", status_code=201)
def create_repository(payload: RepositoryCreate, x_cryless_user: str | None = Header(None)):
    """«cryless create repository <имя>»: регистрирует репозиторий на сервере."""
    username = (x_cryless_user or "").strip()
    if not username:
        return JSONResponse(
            {"detail": "Не указан пользователь (заголовок X-Cryless-User)"},
            status_code=401,
        )
    if not USERNAME_RE.match(username):
        return JSONResponse(
            {"detail": "Ник: 2–32 символа, латиница, цифры, _ . -"},
            status_code=422,
        )

    name = (payload.name or "").strip()
    if not REPO_NAME_RE.match(name) or name.endswith("."):
        return JSONResponse(
            {
                "detail": (
                    "Недопустимое имя репозитория: только латиница, цифры, точка, "
                    "дефис и подчёркивание — без пробелов и кириллицы"
                )
            },
            status_code=422,
        )

    user = db.get_user_by_name(username) or db.create_user(username, "pi")
    existing = db.find_project(user["id"], name)
    if existing and not payload.force:
        return JSONResponse(
            {
                "detail": (
                    f"Репозиторий «{name}» уже существует. "
                    "Используйте --force, чтобы пересоздать и получить новый токен"
                ),
                "id": existing["id"],
            },
            status_code=409,
        )

    platform = _platform()
    dest = platform.UPLOAD_DIR / f"{username}__{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}"
    dest.mkdir(parents=True, exist_ok=True)

    project = db.register_project(username, name, str(dest))
    token = db.issue_repo_token(project["id"], username)
    db.log_action(username, "repo_create", f"репозиторий «{name}»", name)
    return {
        "id": project["id"],
        "name": name,
        "owner": username,
        "token": token,
        "recreated": bool(existing),
        "url": f"/project/{project['id']}",
        "storage": f"{username}__{name}",
    }


# ---------- загрузка данных ----------

@router.post("/repositories/{name}/data")
async def load_data(
    name: str,
    archive: UploadFile = File(...),
    analyze: bool = Form(True),
    authorization: str | None = Header(None),
):
    """«cryless load data <имя>»: принимает архив проекта и раскладывает его.

    Архив целиком заменяет содержимое репозитория (как git push обновляет
    ветку). По умолчанию сразу запускается анализ, чтобы Pi увидел диаграмму.
    """
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        return JSONResponse(
            {"detail": "Нет токена репозитория (Authorization: Bearer <token>)"},
            status_code=401,
        )
    resolved = db.resolve_repo_token(token)
    if not resolved:
        return JSONResponse(
            {
                "detail": (
                    "Токен недействителен. Пересоздайте репозиторий: "
                    "cryless create repository <имя> --force"
                )
            },
            status_code=401,
        )

    project = db.get_project(resolved["repo_id"])
    if not project:
        return JSONResponse({"detail": "Репозиторий не найден"}, status_code=404)
    if project["name"] != name:
        return JSONResponse(
            {
                "detail": (
                    f"Токен выдан для репозитория «{project['name']}», "
                    f"а данные загружаются в «{name}»"
                )
            },
            status_code=403,
        )

    data = await archive.read()
    if not data:
        return JSONResponse({"detail": "Пустой архив"}, status_code=422)
    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"detail": f"Архив больше {_human(MAX_UPLOAD_BYTES)}"}, status_code=413
        )

    platform = _platform()
    dest = Path(project["source"])
    staging = platform.DATA_DIR / f".cryless-upload-{project['id']}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        platform._safe_extract_zip(data, staging)
    except (zipfile.BadZipFile, ValueError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        return JSONResponse({"detail": f"Ошибка архива: {e}"}, status_code=422)

    try:
        # Замена содержимого репозитория: сначала готовим новое рядом,
        # и только потом убираем старое — неудачная распаковка ничего не ломает.
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        staging.rename(dest)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        return JSONResponse({"detail": f"Не удалось сохранить данные: {e}"}, status_code=500)

    files = _count_files(dest)
    db.log_action(
        resolved["username"],
        "repo_load",
        f"{files} файл(ов), {_human(len(data))}",
        project["name"],
    )

    result = {
        "id": project["id"],
        "name": project["name"],
        "owner": resolved["username"],
        "files": files,
        "size": len(data),
        "url": f"/project/{project['id']}",
        "analyzed": False,
        "diagram_kind": None,
        "diagram_url": None,
        "analysis_error": None,
    }
    if analyze:
        try:
            # gitingest внутри вызывает asyncio.run(), поэтому анализ нельзя
            # запускать в текущем event loop — уводим его в отдельный поток.
            analysis = await run_in_threadpool(
                platform.analyze_and_export, db.get_project(project["id"])
            )
        except Exception as e:  # noqa: BLE001 — данные приняты, анализ не критичен
            result["analysis_error"] = str(e)
        else:
            result["analyzed"] = True
            result["diagram_kind"] = analysis["diagram_kind"]
            result["diagram_url"] = f"/project/{project['id']}/diagram"
            db.log_action(
                resolved["username"],
                "analyze",
                f"проект «{project['name']}» (cryless)",
                project["name"],
            )
    return result
