"""Team Server — локальная платформа в стиле GitHub с анализом через gitingest.

Режимы работы:
  * Host  — компьютер пользователя становится хостом для всех: выполняет всю
            серверную работу, хранит у себя проекты Pi и отдаёт по ним результаты.
  * Pi    — обычный пользователь: отправляет свой проект на платформу,
            получает структуру проекта (PNG), может скачивать данные с Host.
"""
import io
import re
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .diagram_gen import tree_to_mermaid
from .image_gen import tree_to_image

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"
UPLOAD_DIR = DATA_DIR / "projects"
EXPORT_DIR = DATA_DIR / "exports"

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

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")


def _bootstrap() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    # Team Server доступен по IP в локальной сети — расширяем allowed hosts
    # для интегрированного сайта ( middleware читает env при импорте).
    import os

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
        return resp
    db.create_user(nick, role="pi")
    resp = RedirectResponse("/choose-role", status_code=303)
    resp.set_cookie("nick", nick, httponly=True)
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
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/logout")
def logout():
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
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "my_projects": my_projects,
            "all_projects": all_projects,
            "users": users,
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
    return RedirectResponse("/dashboard", status_code=303)


# ---------- анализ через gitingest ----------

def _run_gitingest(path_or_url: str) -> tuple[str, str, str]:
    from gitingest import ingest

    return ingest(path_or_url)


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
    """gitingest анализирует сохранённый проект и возвращает структуру + PNG."""
    user = require_user(request)
    if not user:
        return RedirectResponse("/")
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"detail": "Проект не найден"}, status_code=404)

    src_path = Path(project["source"])
    target = str(src_path)
    url_file = src_path / ".source_url"
    if src_path.exists() and any(p for p in src_path.iterdir() if p.name != ".source_url"):
        target = str(src_path)
    elif url_file.exists():
        target = url_file.read_text(encoding="utf-8").strip()

    try:
        summary, tree, content = _run_gitingest(target)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"detail": f"Ошибка анализа: {e}"}, status_code=500)

    png = tree_to_image(tree, f"{project['owner']}/{project['name']}", summary.splitlines()[0] if summary else "")
    export = EXPORT_DIR / f"project_{project_id}_tree.png"
    export.write_bytes(png)
    digest = EXPORT_DIR / f"project_{project_id}_digest.txt"
    digest.write_text(content, encoding="utf-8")

    # gitdiagram-стиль: mermaid-диаграмма по дереву проекта (рендер в браузере)
    tree_body = "\n".join(
        ln for ln in tree.splitlines() if ln.strip() and not ln.startswith("Directory structure")
    )
    diagram_code = tree_to_mermaid(tree_body or tree)

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "user": user,
            "project": project,
            "summary": summary,
            "tree": tree,
            "diagram_code": diagram_code,
            "png_name": export.name,
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
    export = EXPORT_DIR / f"project_{project_id}_tree.png"
    if not export.exists():
        return JSONResponse({"detail": "Сначала проанализируйте проект"}, status_code=409)
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
    digest = EXPORT_DIR / f"project_{project_id}_digest.txt"
    if not digest.exists():
        return JSONResponse({"detail": "Сначала проанализируйте проект"}, status_code=409)
    return Response(
        content=digest.read_bytes(),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{project["name"]}_digest.txt"'},
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
    digest = EXPORT_DIR / f"project_{project_id}_digest.txt"
    content = digest.read_text(encoding="utf-8") if digest.exists() else None
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
