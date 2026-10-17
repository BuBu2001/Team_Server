"""Проверка имён: правила «без пробелов и без кириллицы».

Имя репозитория становится именем каталога проекта на сервере
(``data/projects/<ник>__<имя>``), поэтому оно ограничено латиницей, цифрами и
символами ``. _ -``. Ник пользователя подчиняется тем же правилам, что и в
веб-панели (2–32 символа).
"""
from __future__ import annotations

import re

from .errors import CrylessError

#: Разрешённые символы имени репозитория (без пробелов и кириллицы).
ALLOWED = "латиница (A-Z, a-z), цифры (0-9), точка (.), дефис (-), подчёркивание (_)"

MAX_REPO_LEN = 64
MAX_USER_LEN = 32


def _has_bad_chars(value: str) -> list[str]:
    return [
        ch
        for ch in value
        if not (ch.isascii() and (ch.isalnum() or ch in "._-"))
    ]


def describe_bad_chars(value: str) -> str:
    """Человеческое пояснение, что именно не так с именем."""
    bad = _has_bad_chars(value)
    if not bad:
        return ""
    if any(" " in ch or ch in "\t\n" for ch in bad):
        return "есть пробелы"
    if any(not ch.isascii() for ch in bad):
        return "есть кириллица или другие нелатинские символы"
    return f"есть недопустимые символы: {''.join(sorted(set(bad)))}"


def validate_repo_name(name: str, *, what: str = "репозитория") -> str:
    """Проверяет имя репозитория и возвращает его же (обрезанное)."""
    name = (name or "").strip()
    if not name:
        raise CrylessError(f"Пустое имя {what}")
    if len(name) > MAX_REPO_LEN:
        raise CrylessError(f"Имя {what} длиннее {MAX_REPO_LEN} символов")
    problem = describe_bad_chars(name)
    if problem or not name[0].isascii() or not name[0].isalnum():
        reason = problem or "начинается не с латинской буквы или цифры"
        raise CrylessError(
            f"Недопустимое имя {what} «{name}»: {reason}.\n"
            f"  Разрешено только: {ALLOWED} — без пробелов и без кириллицы.\n"
            "  Примеры: my_project, api-v2, site.ru"
        )
    if name.endswith("."):
        raise CrylessError(f"Имя {what} не может заканчиваться точкой")
    if ".." in name:
        raise CrylessError(f"Имя {what} не может содержать «..»")
    return name


def validate_username(nick: str) -> str:
    """Ник пользователя — как в веб-панели: 2–32 символа, латиница/цифры/._-."""
    nick = (nick or "").strip()
    if not nick:
        raise CrylessError("Не задан ник пользователя (--user или CRYLESS_USER)")
    if not (2 <= len(nick) <= MAX_USER_LEN):
        raise CrylessError(
            f"Ник «{nick}»: нужно 2–{MAX_USER_LEN} символа"
        )
    problem = describe_bad_chars(nick)
    if problem:
        raise CrylessError(
            f"Недопустимый ник «{nick}»: {problem}.\n"
            f"  Разрешено только: {ALLOWED}"
        )
    return nick


def looks_ascii_clean(value: str) -> bool:
    """True, если строка без пробелов и без кириллицы (для предупреждений)."""
    return not _has_bad_chars(value)


#: Простая транслитерация кириллицы (для авто-нормализации имён).
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify_repo_name(name: str) -> str:
    """Приводит произвольную строку к допустимому имени репозитория.

    Примеры:
        "C++ flag generator" -> "cpp-flag-generator"
        "my project"         -> "my-project"
        "мой_проект"         -> "moy_proekt"
    """
    s = (name or "").strip().lower()
    s = "".join(_TRANSLIT.get(ch, ch) for ch in s)
    # "++" -> "pp" (чтобы «c++» превратилось в «cpp»), одиночный "+" -> "-"
    s = s.replace("++", "pp").replace("+", "-")
    # всё недопустимое превращаем в дефис
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    # схлопываем повторы и чистим края
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"\.{2,}", ".", s)
    s = s[:MAX_REPO_LEN].strip("-._")
    return s or "project"


def normalize_repo_name(
    name: str, *, what: str = "репозитория"
) -> tuple[str, str | None]:
    """Возвращает ``(валидное_имя, исходное_имя_если_оно_было_изменено)``.

    Если имя уже допустимо — возвращается оно же без изменений (и ``None``).
    Если нет — оно приводится к допустимому виду через :func:`slugify_repo_name`.
    """
    raw = (name or "").strip()
    try:
        return validate_repo_name(raw, what=what), None
    except CrylessError:
        slug = slugify_repo_name(raw)
        return validate_repo_name(slug, what=what), raw
