"""Мост между веб-панелью (app_platform) и модулем анализа кода
``gitdiagram-static`` (пакет ``staticdiagram``).

Без этого модуля gitdiagram-static был доступен только консоли
GettingStarted, и «диаграмма кода» в веб-панели отсутствовала: там строился
лишь mermaid по дереву каталогов. Здесь мы вызываем статический анализ кода
ровно так же, как это делает хост в getting_started/core.py, и возвращаем
готовый mermaid-код для рендера в браузере (mermaid.min.js).

Модуль мягко деградирует: если ``staticdiagram`` недоступен или проект не
поддаётся анализу — возвращается ``(None, причина)``, а вызывающий код
откатывается на диаграмму по дереву.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATICDIAGRAM_SRC = REPO_ROOT / "gitdiagram-static" / "src"


def ensure_importable() -> bool:
    """Добавляет gitdiagram-static/src в sys.path (без pip install)."""
    if not STATICDIAGRAM_SRC.is_dir():
        return False
    if str(STATICDIAGRAM_SRC) not in sys.path:
        sys.path.insert(0, str(STATICDIAGRAM_SRC))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return True


def code_mermaid(path: str | Path) -> tuple[str | None, str]:
    """Строит mermaid-диаграмму архитектуры кода для локального проекта.

    Возвращает ``(mermaid, "")`` при успехе либо ``(None, причина)``, если
    модуль недоступен или анализ не дал результата.
    """
    if not ensure_importable():
        return None, "gitdiagram-static не найден (нет каталога gitdiagram-static/src)"
    try:
        from staticdiagram import generate  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return None, f"gitdiagram-static недоступен: {exc}"

    try:
        result = generate(str(path), formats=["mermaid"])
    except Exception as exc:  # noqa: BLE001
        return None, f"анализ кода не удался: {str(exc).splitlines()[0]}"

    code = str(getattr(result, "mermaid", "") or "")
    if not code.strip():
        return None, "gitdiagram-static не вернул диаграмму"
    return code, ""
