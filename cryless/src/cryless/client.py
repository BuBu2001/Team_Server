"""HTTP-клиент cryless: обращения к серверу Team Server.

Соответствует API в ``app_platform/cryless_api.py``:

    GET  /api/cryless/ping
    POST /api/cryless/repositories
    POST /api/cryless/repositories/{name}/data

Сеть недоступна / сервер молчит — превращаем в понятный CrylessError,
чтобы пользователь видел не трассировку, а подсказку.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from .errors import CrylessError

DEFAULT_TIMEOUT = 20.0
#: Анализ на сервере (gitingest + диаграмма) может занять заметное время.
UPLOAD_TIMEOUT = 600.0


class TeamServerClient:
    """Тонкая обёртка над HTTP API Team Server."""

    def __init__(self, server: str, timeout: float = DEFAULT_TIMEOUT):
        self.server = server.rstrip("/")
        self.timeout = timeout

    # ---------- служебное ----------

    def _error_from_response(self, response: httpx.Response) -> CrylessError:
        detail = ""
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("message") or "")
        if not detail:
            detail = (response.text or "").strip()[:300]
        messages = {
            401: "Сервер не принял доступ",
            403: "Сервер отклонил доступ",
            404: "Сервер не нашёл репозиторий",
            409: "Конфликт на сервере",
            413: "Слишком большой архив",
            422: "Сервер отклонил данные",
        }
        prefix = messages.get(response.status_code, f"Сервер вернул {response.status_code}")
        return CrylessError(f"{prefix}: {detail}" if detail else prefix)

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            with httpx.Client(base_url=self.server, timeout=self.timeout) as client:
                return client.request(method, url, **kwargs)
        except httpx.ConnectError as e:
            raise CrylessError(
                f"Нет соединения с сервером {self.server}\n"
                "  Проверьте: сервер запущен на Host (./run.sh), адрес и порт верны,\n"
                "  машины в одной сети (ping), порт не закрыт файрволом."
            ) from e
        except httpx.TimeoutException as e:
            raise CrylessError(
                f"Сервер {self.server} не ответил за {self.timeout:.0f} с"
            ) from e
        except httpx.HTTPError as e:
            raise CrylessError(f"Ошибка обращения к серверу {self.server}: {e}") from e

    # ---------- команды ----------

    def ping(self) -> tuple[dict, float]:
        """«connection check»: вернуть (данные сервера, задержка в секундах)."""
        started = time.perf_counter()
        response = self._request("GET", "/api/cryless/ping")
        latency = time.perf_counter() - started
        if response.status_code != 200:
            raise self._error_from_response(response)
        return response.json(), latency

    def create_repository(
        self, name: str, user: str, force: bool = False, token: str = ""
    ) -> dict:
        """Создаёт репозиторий; при ``force`` сервер требует текущий токен."""
        headers = {"X-Cryless-User": user}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self._request(
            "POST",
            "/api/cryless/repositories",
            json={"name": name, "force": force},
            headers=headers,
        )
        if response.status_code != 201:
            raise self._error_from_response(response)
        return response.json()

    def load_data(
        self,
        name: str,
        token: str,
        archive: Path,
        analyze: bool = True,
    ) -> dict:
        self.timeout = UPLOAD_TIMEOUT  # загрузка + анализ
        with archive.open("rb") as fh:
            response = self._request(
                "POST",
                f"/api/cryless/repositories/{name}/data",
                headers={"Authorization": f"Bearer {token}"},
                files={"archive": (archive.name, fh, "application/zip")},
                data={"analyze": "true" if analyze else "false"},
            )
        if response.status_code != 200:
            raise self._error_from_response(response)
        return response.json()
