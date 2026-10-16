"""Настройки cryless на машине пользователя.

  * глобальные — ``$CRYLESS_HOME/config.json`` или ``~/.config/cryless/config.json``
    (адрес сервера и ник: чтобы не повторять их в каждой команде);
  * приоритет у флагов команд и переменных окружения ``CRYLESS_SERVER`` /
    ``CRYLESS_USER``.

Локальные настройки конкретного репозитория лежат в самом проекте —
``.cryless/config.json`` (см. localrepo.py), там же токен доступа.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_SERVER = "http://localhost:8000"
ENV_SERVER = "CRYLESS_SERVER"
ENV_USER = "CRYLESS_USER"
ENV_HOME = "CRYLESS_HOME"


def config_dir() -> Path:
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "cryless"


def config_file() -> Path:
    return config_dir() / "config.json"


def load_global() -> dict:
    path = config_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_global(**values) -> None:
    """Дописывает значения в глобальный конфиг (создаёт каталог при необходимости)."""
    values = {k: v for k, v in values.items() if v}
    if not values:
        return
    data = load_global()
    data.update(values)
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_server(cli_value: str | None = None, repo_value: str | None = None) -> str:
    """Адрес сервера: флаг → окружение → репозиторий → глобальный конфиг → умолчание."""
    for candidate in (
        cli_value,
        os.environ.get(ENV_SERVER),
        repo_value,
        load_global().get("server"),
        DEFAULT_SERVER,
    ):
        if candidate:
            return str(candidate).rstrip("/")
    return DEFAULT_SERVER


def resolve_user(cli_value: str | None = None, repo_value: str | None = None) -> str | None:
    """Ник: флаг → окружение → репозиторий → глобальный конфиг."""
    for candidate in (
        cli_value,
        os.environ.get(ENV_USER),
        repo_value,
        load_global().get("user"),
    ):
        if candidate:
            return str(candidate)
    return None
