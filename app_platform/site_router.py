"""Интеграция сайта gitingest в Team Server.

Монтирует полноценный сайт gitingest под ``/site`` (главная, форма анализа
репозиториев, Swagger-доки ``/site/docs``, API ``/site/api/ingest``).

Нюансы, которые здесь решаются:
  * приложение сайта монтируется ПОД ПУТЁМ, поэтому Starlette не вычисляет
    root_path автоматически — подстраховываемся middleware ниже;
  * сайт использует относительные пути к файлам (``static/robots.txt``),
    поэтому рабочий каталог процесса фиксируется в корне репозитория;
  * статика сайта доступна на общем пути ``/site/static/*``.

Анализ произвольного репозитория из основного сайта Team Server идёт через
JSON-API ``POST /api/ingest`` (см. app_platform/main.py) — отдельной
HTML-страницы ``/analyze`` здесь больше нет.
"""
import os
import sys
from pathlib import Path

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


# ---------- сам сайт gitingest под /site ----------
# ВНИМАНИЕ: Mount не копируется при include_router (ограничение FastAPI),
# поэтому монтирование выполняется в app_platform/main.py через mount_site(app).

def mount_site(target_app) -> None:
    """Монтирует сайт gitingest на путь /site переданного FastAPI-приложения."""
    target_app.mount("/site", _wrapped_site_app)
