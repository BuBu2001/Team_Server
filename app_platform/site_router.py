"""Интеграция сайта gitingest в Team Server.

Маршрутизатор ``/site`` проброшен в FastAPI-приложение из ``src/server/main.py``
— полноценный сайт gitingest (главная, форма анализа репозиториев,
Swagger-доки ``/site/docs``, API ``/site/api/ingest``).

Нюансы, которые здесь решаются:
  * приложение сайта монтируется ПОД ПУТЁМ, поэтому Starlette не вычисляет
    root_path автоматически — подстраховываемся middleware ниже;
  * сайт использует относительные пути к файлам (``static/robots.txt``),
    поэтому рабочий каталог процесса фиксируется в корне репозитория;
  * статика сайта доступна на общем пути ``/site/static/*``;
  * добавлены страницы ``/analyze`` (GET/POST) — анализ git-репозитория
    средствами сайта прямо из панели Team Server.
"""
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_SRC = REPO_ROOT / "src"

# Сайт использует относительный путь "static/..." (см. src/server/main.py),
# поэтому рабочий каталог процесса должен указывать на каталог со статикой.
# Если он уже корректный (например, Docker WORKDIR /app) — не трогаем его.
if not (Path.cwd() / "static").is_dir():
    os.chdir(SITE_SRC)

# Пути для импорта пакетов gitingest (src/gitingest) и сайта (src/server).
for p in (str(REPO_ROOT), str(SITE_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

# TrustedHostMiddleware в сайте читает ALLOWED_HOSTS во время импорта
# src/server/main.py — задаём значение ДО импорта, чтобы запросы с любых
# хостов (IP сервера, домен Team Server, localhost) проходили проверку.
os.environ.setdefault("ALLOWED_HOSTS", "*")

from server.main import app as site_app  # noqa: E402

router = APIRouter()

templates = Jinja2Templates(directory=REPO_ROOT / "app_platform" / "templates")


class RootPathMiddleware:
    """ASGI-обёртка: подстраховка root_path для смонтированного сайта
    (если uvicorn запущен без --root-path, например в тестах или скриптах)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not scope.get("root_path", ""):
            path = scope.get("path", "")
            if path.startswith("/site"):
                remaining = path[len("/site"):] or "/"
                if not remaining.startswith("/"):
                    remaining = "/" + remaining
                scope = {**scope, "root_path": "/site"}
        await self.app(scope, receive, send)


site_app.mounted_root_path = "/site"  # noqa: ERA001 (маркер для отладки)
_wrapped_site_app = RootPathMiddleware(site_app)


# ---------- страница анализа репозитория для панели Team Server ----------

@router.get("/analyze", response_class=HTMLResponse)
async def analyze_page(request: Request):
    from app_platform.main import current_user  # локальный импорт против цикла

    user = current_user(request)
    if not user:
        return RedirectResponse("/")
    return templates.TemplateResponse(
        request,
        "site_analyze.html",
        {"user": user},
    )


@router.post("/analyze", response_class=HTMLResponse)
async def analyze_submit(
    request: Request,
    url: str = Form(...),
    max_size: int = Form(100),
    token: str = Form(""),
):
    from app_platform.main import current_user

    user = current_user(request)
    if not user:
        return RedirectResponse("/")

    error = None
    result = None
    try:
        from server.models import PatternType
        from server.query_processor import process_query

        res = await process_query(
            input_text=url.strip(),
            max_file_size=max_size,
            pattern_type=PatternType.EXCLUDE,
            pattern="",
            token=token.strip() or None,
        )
        # IngestSuccessResponse имеет поля summary/tree/digest_url
        if hasattr(res, "summary"):
            result = res
        else:
            error = getattr(res, "error", "Неизвестная ошибка анализа")
    except Exception as exc:  # noqa: BLE001
        error = f"Ошибка анализа: {exc!s}"

    return templates.TemplateResponse(
        request,
        "site_analyze.html",
        {
            "user": user,
            "url": url,
            "result": result,
            "error": error,
        },
    )


# ---------- сам сайт gitingest под /site ----------
# ВНИМАНИЕ: Mount не копируется при include_router (ограничение FastAPI),
# поэтому монтирование выполняется в app_platform/main.py через mount_site(app).


def mount_site(target_app) -> None:
    """Монтирует сайт gitingest на путь /site переданного FastAPI-приложения."""
    target_app.mount("/site", _wrapped_site_app)

