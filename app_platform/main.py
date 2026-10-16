"""Team Server — локальная платформа в стиле GitHub с анализом через gitingest.

Режимы работы:
  * Host  — компьютер пользователя становится хостом для всех: выполняет всю
            серверную работу, хранит у себя проекты Pi и отдаёт по ним результаты.
  * Pi    — обычный пользователь: отправляет свой проект на платформу,
            получает структуру проекта (PNG), может скачивать данные с Host.
"""
import io
import os
import re
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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

app = FastAPI(title="Team Server", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Интегрированный сайт gitingest: /site/* + страница /analyze (импорт в конце,
# чтобы избежать циклического импорта — site_router использует current_user).
from .site_router import mount_site  # noqa: E402
from .site_router import router as site_router  # noqa: E402

app.include_router(site_router)
# Mount не копируется через include_router — монтируем сайт напрямую в app.
mount_site(app)

# HTTP-API библиотеки команд cryless (CLI на машине Pi: create/load/connection).
from .cryless_api import router as cryless_router  # noqa: E402

app.include_router(cryless_router)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")


def _bootstrap() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    PI_FILES_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    # Team Server доступен по IP в локальной сети — расширяем allowed hosts
    # для интегрированного сайта ( middleware читает env при импорте).
    hosts = {h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
    hosts |= {"localhost", "127.0.0.1", "*"}
    os.environ["ALLOWED_HOSTS"] = ",".join(sorted(hosts))


@app.on_event("startup")
def startup() -> None:
    _bootstrap()


# ---------- cookie-сессия (без паролей) ----------

def current_user(request: Request) -> dict | None:
    nick = request.cookies.get("nick")
    if not nick:
        return None
    return db.get_user_by_name(nick)


def require_user(request: Request):
    user = current_user(request)
    if user is None:
        return None
    return user


def can_access_project(user: dict, project: dict) -> bool:
    """Доступ к материалам проекта: владелец или хост.

    Pi работает только со своими проектами и с общей витриной, а скачивать
    чужие данные (digest/PNG/API) может только Host — он сервер этой сети.
    """
    return user["role"] == "host" or project["owner_id"] == user["id"]


# ---------- регистрация / вход ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    user = current_user(request)
    if user:
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/register")
def register(request: Request, nick: str = Form(...)):
    nick = nick.strip()
    if not USERNAME_RE.match(nick):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Ник: 2–32 символа, латиница, цифры, _ . -"},
            status_code=422,
        )
    existing = db.get_user_by_name(nick)
    if existing:
        # регистрация без паролей: если ник занят — просто вход
        resp = RedirectResponse("/dashboard", status_code=303)
        resp.set_cookie("nick", nick, httponly=True)
        db.log_action(nick, "login", "вход в веб-панель", "")
        return resp
    db.create_user(nick, role="pi")
    resp = RedirectResponse("/choose-role", status_code=303)
    resp.set_cookie("nick", nick, httponly=True)
    db.log_action(nick, "register", "регистрация в веб-панели", "")
    return resp


@app.get("/choose-role", response_class=HTMLResponse)
def choose_role_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "role.html", {"user": user})


@app.post("/choose-role")
def choose_role(request: Request, role: str = Form(...)):
    user = require_user(request)
    if not user:
        return RedirectResponse("/")
    if role not in ("host", "pi"):
        return JSONResponse({"detail": "bad role"}, status_code=422)
    db.set_role(user["id"], role)
    db.log_action(user["username"], "role", f"выбран режим {role}", "")
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/logout")
def logout(request: Request):
    user = current_user(request)
    if user:
        db.log_action(user["username"], "logout", "выход из веб-панели", "")
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("nick")
    return resp


# ---------- панель ----------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/")
    my_projects = db.list_projects(owner_id=user["id"])
    all_projects = db.list_projects()
    users = db.list_users()
    actions = db.list_actions(
        user=None if user["role"] == "host" else user["username"], limit=10
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "my_projects": my_projects,
            "all_projects": all_projects,
            "users": users,
            "actions": actions,
        },
    )


# ---------- загрузка проекта ----------

def _safe_extract_zip(data: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            if member.startswith("/") or ".." in Path(member).parts:
                raise ValueError(f"Недопустимый путь в архиве: {member}")
        zf.extractall(dest)


@app.post("/upload")
async def upload_project(
    request: Request,
    name: str = Form(...),
    source: str = Form(""),          # URL репозитория (git/http) — для Host-режима
    archive: UploadFile | None = File(None),
):
    """Pi отправляет проект: zip-архив с папкой ИЛИ ссылка на репозиторий."""
    user = require_user(request)
    if not user:
        return RedirectResponse("/")

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
        # сохраняем ссылку — клонирование произойдёт при анализе
        (dest / ".source_url").write_text(source.strip(), encoding="utf-8")
    else:
        return JSONResponse(
            {"detail": "Загрузите zip-архив проекта или укажите ссылку на репозиторий"},
            status_code=422,
        )

    db.add_project(user["id"], name, str(dest))
    db.log_action(user["username"], "upload", f"проект «{name}»", name)
    return RedirectResponse("/dashboard", status_code=303)


# ---------- анализ через gitingest ----------

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

    Общий код веб-панели (маршрут /project/{id}/analyze) и HTTP-API cryless
    (``cryless load data`` анализирует загруженные данные на сервере), поэтому
    и панель, и консоль Pi видят одни и те же диаграммы.

    Возвращает словарь для шаблона result.html; поднимает SourceError (422)
    для неподходящего источника и AnalysisError (500), если анализ упал.
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

    return {
        "summary": summary,
        "tree": tree,
        "diagram_code": diagram_code,
        "diagram_kind": "code" if code_diagram else "tree",
        "diagram_note": code_error if code_diagram is None and code_error else "",
        "png_name": export.name,
    }



@app.get("/project/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: int):
    user = require_user(request)
    if not user:
        return RedirectResponse("/")
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    return templates.TemplateResponse(
        request, "project.html", {"user": user, "project": project}
    )


@app.post("/project/{project_id}/analyze")
def analyze_project(request: Request, project_id: int):
    """gitingest анализирует сохранённый проект и возвращает структуру + PNG.

    Дополнительно строится диаграмма кода через gitdiagram-static (тот же
    модуль, что использует консоль Host ⇄ Pi); если код не поддаётся анализу
    или диаграмма пуста — показывается диаграмма по дереву файлов.
    """
    user = require_user(request)
    if not user:
        return RedirectResponse("/")
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)

    try:
        result = analyze_and_export(project)
    except SourceError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    except AnalysisError as e:
        return JSONResponse({"detail": str(e)}, status_code=500)

    db.log_action(
        user["username"], "analyze", f"проект «{project['name']}»", project["name"]
    )
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "user": user,
            "project": project,
            "summary": result["summary"],
            "tree": result["tree"],
            "diagram_code": result["diagram_code"],
            "diagram_kind": result["diagram_kind"],
            "diagram_note": result["diagram_note"],
            "png_name": result["png_name"],
        },
    )


@app.get("/download/tree/{project_id}", response_class=Response)
def download_tree(request: Request, project_id: int):
    """PNG структуры проекта — сохранить на свой ПК."""
    user = require_user(request)
    if not user:
        return RedirectResponse("/")
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    if not can_access_project(user, project):
        return JSONResponse({"detail": "Недостаточно прав (нужен режим Host)"}, status_code=403)
    export = EXPORT_DIR / f"project_{project_id}_tree.png"
    if not export.exists():
        return JSONResponse({"detail": "Сначала проанализируйте проект"}, status_code=409)
    db.log_action(user["username"], "download", "PNG структуры", project["name"])
    return Response(
        content=export.read_bytes(),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{project["name"]}_tree.png"'},
    )


@app.get("/download/digest/{project_id}", response_class=Response)
def download_digest(request: Request, project_id: int):
    """Полный текстовый дамп проекта (digest) — как в gitingest."""
    user = require_user(request)
    if not user:
        return RedirectResponse("/")
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
        content=digest.read_bytes(),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{project["name"]}_digest.txt"'},
    )


@app.get("/project/{project_id}/diagram", response_class=HTMLResponse)
def project_diagram(request: Request, project_id: int):
    """Отдельная страница с mermaid-диаграммой проекта (код или структура).

    Диаграмма сохраняется при анализе (EXPORT_DIR/project_<id>_diagram.mmd),
    поэтому страница отдаёт её без повторного тяжёлого статического анализа.
    """
    user = require_user(request)
    if not user:
        return RedirectResponse("/")
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    diagram_file = EXPORT_DIR / f"project_{project_id}_diagram.mmd"
    if not diagram_file.exists():
        return RedirectResponse(f"/project/{project_id}")
    db.log_action(user["username"], "diagram", "просмотр диаграммы", project["name"])
    return templates.TemplateResponse(
        request,
        "diagram_page.html",
        {
            "title": f"{project['owner']}/{project['name']}",
            "code": diagram_file.read_text(encoding="utf-8"),
        },
    )


@app.get("/journal", response_class=HTMLResponse)
def journal_page(request: Request):
    """Журнал работы: кто, что и когда делал — общий для веба и консоли.

    Host видит весь журнал команды, Pi — только свои действия.
    """
    user = require_user(request)
    if not user:
        return RedirectResponse("/")
    if user["role"] == "host":
        rows = db.list_actions(limit=200)
    else:
        rows = db.list_actions(user=user["username"], limit=200)
    return templates.TemplateResponse(
        request,
        "journal.html",
        {"user": user, "rows": rows, "scope": "всей команды" if user["role"] == "host" else "ваших действий"},
    )


@app.get("/api/export/{project_id}")
def api_export(request: Request, project_id: int):
    """Host-режим: Pi может запросить данные проекта в JSON по API."""
    user = require_user(request)
    if not user:
        return JSONResponse({"detail": "Не авторизован"}, status_code=401)
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)
    if not can_access_project(user, project):
        return JSONResponse({"detail": "Недостаточно прав (нужен режим Host)"}, status_code=403)
    digest = EXPORT_DIR / f"project_{project_id}_digest.txt"
    content = digest.read_text(encoding="utf-8") if digest.exists() else None
    db.log_action(user["username"], "api_export", "выгрузка JSON", project["name"])
    return {
        "id": project["id"],
        "name": project["name"],
        "owner": project["owner"],
        "added_at": project["added_at"],
        "analyzed": content is not None,
        "digest": content,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
