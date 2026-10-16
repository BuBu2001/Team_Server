"""Ошибки cryless: пользователю показываем текст, а не трассировку."""
from __future__ import annotations


class CrylessError(Exception):
    """Понятная пользователю ошибка (exit code 1)."""


class UsageError(CrylessError):
    """Ошибка использования команды (exit code 2)."""
