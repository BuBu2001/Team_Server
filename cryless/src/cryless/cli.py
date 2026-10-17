"""CLI библиотеки cryless — парсер команд и сами команды.

Структура команд повторяет git-подобный стиль::

    cryless create repository [имя]    создать репозиторий
    cryless load data [имя]            загрузить данные проекта в репозиторий
    cryless connection check           проверить соединение с сервером
    cryless help                       справка
    cryless version                    версия

Все команды запускаются в КОРНЕ проекта; имя репозитория приводится к
допустимому виду автоматически (латиница, цифры, точка, дефис, подчёркивание).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .client import TeamServerClient
from .config import resolve_server, resolve_user, save_global
from .errors import CrylessError, UsageError
from .localrepo import (
    RepoConfig,
    cleanup_pack,
    ensure_project_root,
    find_repo_root,
    human_size,
    load_config,
    now_iso,
    pack_repo,
    project_root_warning,
    save_config,
    write_default_ignore,
)
from .naming import normalize_repo_name, validate_username

HELP = """\
cryless — библиотека команд для Team Server (работает как git).

Использование:
  cryless <команда> [аргументы]

Команды:
  create repository [имя]   создать репозиторий и зарегистрировать его на сервере
  load data [имя]           загрузить данные (файлы проекта) в репозиторий
  connection check          проверить, есть ли соединение с сервером
  help                      показать эту справку
  version                   показать версию cryless

Условия:
  * команду запускают в КОРНЕ проекта — там, где лежат его файлы;
  * имя проекта (репозитория) приводится к допустимому виду автоматически:
    пробелы и «+» заменяются на дефис, кириллица — на латиницу
    («C++ flag generator» → «cpp-flag-generator», «мой проект» → «moy-proekt»);
  * если имя не указано, берётся имя текущей папки.

Примеры:
  cd ~/my_project
  cryless create repository my_project   # создаст .cryless/ и репозиторий на сервере
  cryless load data my_project           # отправит файлы проекта на сервер
  cryless connection check               # проверить связь с сервером

Дополнительно:
  --server URL   адрес сервера (по умолчанию http://localhost:8000,
                 переменная окружения CRYLESS_SERVER)
  --user НИК     ваш ник на сервере (переменная окружения CRYLESS_USER)
  CRYLESS_HOME   каталог настроек (по умолчанию ~/.config/cryless)
"""


# ---------- вывод ----------

def _tty() -> bool:
    return sys.stdout.isatty()


def _c(code: str) -> str:
    return code if _tty() else ""


GREEN, YELLOW, RED, CYAN, OFF = (
    _c("\033[1;32m"),
    _c("\033[1;33m"),
    _c("\033[1;31m"),
    _c("\033[1;36m"),
    _c("\033[0m"),
)


def say(text: str = "") -> None:
    print(text, flush=True)


def ok(text: str) -> None:
    print(f"{GREEN}✓{OFF} {text}", flush=True)


def warn(text: str) -> None:
    print(f"{YELLOW}!{OFF} {text}", flush=True)


def fail(text: str) -> None:
    print(f"{RED}×{OFF} {text}", file=sys.stderr, flush=True)


def title(text: str) -> None:
    print(f"{CYAN}==>{OFF} {text}", flush=True)


DT_FORMATS = ("%Y-%m-%dT%H:%M:%SZ",)


def _fmt_latency(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} мс"
    return f"{seconds:.1f} с"


# ---------- команды ----------

def cmd_help(_args) -> int:
    say(HELP)
    return 0


def cmd_version(_args) -> int:
    say(f"cryless {__version__}")
    return 0


def cmd_connection_check(args) -> int:
    server = resolve_server(args.server)
    say(f"Сервер: {server}")
    client = TeamServerClient(server, timeout=args.timeout)
    data, latency = client.ping()
    ok(f"Соединение есть — {data.get('service', 'сервер')} {data.get('version', '')}".rstrip()
       + f", {_fmt_latency(latency)}")
    if "repositories" in data:
        say(f"  Репозиториев на сервере: {data['repositories']}")
    if data.get("time"):
        say(f"  Время сервера: {data['time']}")
    root = find_repo_root(Path.cwd())
    config = load_config(root) if root else None
    user = resolve_user(args.user, config.user if config else None)
    if user:
        say(f"  Вы: {user}")
    if root and config:
        say(f"  Локальный репозиторий: {config.name} ({root})")
    return 0


def cmd_create_repository(args) -> int:
    root = Path.cwd()
    ensure_project_root(root)

    existing = load_config(root)
    if existing and not args.force:
        raise CrylessError(
            f"Репозиторий уже создан в этом каталоге: «{existing.name}».\n"
            "  Загрузить данные:      cryless load data\n"
            "  Пересоздать (новый токен): cryless create repository <имя> --force"
        )

    name, renamed_from = normalize_repo_name(args.name or root.name)
    if renamed_from:
        warn(f"имя «{renamed_from}» приведено к «{name}» "
             "(допустимы только латиница, цифры, . _ -)")
    server = resolve_server(args.server, existing.server if existing else None)
    user = resolve_user(args.user, existing.user if existing else None)
    if not user:
        raise CrylessError(
            "Не задан ник пользователя.\n"
            "  Укажите его один раз: cryless create repository <имя> --user <ник>\n"
            "  или задайте переменную окружения CRYLESS_USER"
        )
    user = validate_username(user)

    warning = project_root_warning(root)
    if warning:
        warn(warning)
    say(f"Сервер: {server}")
    say(f"Репозиторий: {user}/{name}")
    say("Создаю…")

    client = TeamServerClient(server, timeout=args.timeout)
    data = client.create_repository(name, user, force=args.force)

    config = RepoConfig(
        name=data.get("name", name),
        server=server,
        user=user,
        token=data.get("token", ""),
        project_id=data.get("id"),
        created_at=now_iso(),
    )
    path = save_config(root, config)
    ignore = write_default_ignore(root)
    save_global(server=server, user=user)

    if data.get("recreated"):
        ok(f"Репозиторий «{config.name}» пересоздан (выдан новый токен)")
    else:
        ok(f"Репозиторий «{config.name}» создан")
    say(f"  Владелец:  {user}")
    say(f"  Сервер:    {server}")
    say(f"  Панель:    {server}{data.get('url', '')}")
    say(f"  Настройки: {path.relative_to(root)}" + (" (+ создан .crylessignore)" if ignore else ""))
    say(f"  Далее:     cryless load data {config.name}")
    return 0


def cmd_load_data(args) -> int:
    root = Path.cwd().resolve()
    config = load_config(root)
    if config is None:
        parent = find_repo_root(root.parent)
        if parent is not None:
            raise CrylessError(
                f"Вы не в корне проекта: репозиторий cryless найден выше ({parent}).\n"
                f"  Запустите команду в корне проекта: cd {parent} && cryless load data"
            )
        raise CrylessError(
            "В этом каталоге нет репозитория cryless.\n"
            "  Сначала создайте его в корне проекта: cryless create repository <имя>"
        )
    ensure_project_root(root)

    name, _ = normalize_repo_name(args.name or config.name)
    if args.name and name != config.name:
        raise CrylessError(
            f"Локальный репозиторий называется «{config.name}», а данные грузятся "
            f"в «{args.name}».\n"
            "  Загрузите в свой репозиторий: cryless load data\n"
            "  Или пересоздайте локальный:   cryless create repository <имя> --force"
        )
    if not config.token:
        raise CrylessError(
            "В .cryless/config.json нет токена доступа.\n"
            "  Пересоздайте репозиторий, чтобы получить новый:\n"
            "  cryless create repository <имя> --force"
        )

    server = resolve_server(args.server, config.server)
    warning = project_root_warning(root)
    if warning:
        warn(warning)

    title("Собираю данные проекта")
    result = pack_repo(root)
    try:
        say(f"  Файлов: {result.files}, архив: {human_size(result.size)}")
        if result.skipped:
            shown = result.skipped[:5]
            for path, reason in shown:
                warn(f"пропущено: {path} ({reason})")
            if len(result.skipped) > len(shown):
                warn(f"и ещё {len(result.skipped) - len(shown)} пропущено (см. .crylessignore)")
        if result.files == 0:
            raise CrylessError(
                "Нечего отправлять: все файлы попали под .crylessignore.\n"
                "  Проверьте файл .crylessignore в корне проекта."
            )

        say(f"Сервер: {server}")
        title("Отправляю на сервер")
        client = TeamServerClient(server, timeout=args.timeout)
        data = client.load_data(config.name, config.token, result.archive, analyze=not args.no_analyze)
    finally:
        cleanup_pack(result)

    ok(f"Данные загружены: {data.get('files', result.files)} файл(ов), {human_size(data.get('size', result.size))}")
    say(f"  Репозиторий: {data.get('owner', config.user)}/{data.get('name', config.name)}")
    url = data.get("url", "")
    say(f"  Панель:      {server}{url}")
    if data.get("analyzed"):
        kind = "диаграмма архитектуры кода" if data.get("diagram_kind") == "code" else "диаграмма структуры"
        say(f"  Диаграмма:   {server}{data.get('diagram_url', url)}  ({kind})")
    elif data.get("analysis_error"):
        warn(f"данные приняты, но анализ не удался: {data['analysis_error']}")
    else:
        say("  Анализ не запускался (--no-analyze)")
    say("  Открыть панель: " + server)
    return 0


# ---------- парсер ----------

class _Parser(argparse.ArgumentParser):
    """Парсер, который не убивает процесс, а поднимает UsageError."""

    def error(self, message: str):  # noqa: D401
        raise UsageError(f"{message}\n\nПодсказка: cryless help")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="cryless", description="Команды Team Server в стиле git", add_help=False)
    sub = parser.add_subparsers(dest="group")

    common = {"add_help": True}

    create = sub.add_parser("create", help="создать репозиторий", **common)
    create_sub = create.add_subparsers(dest="command")
    create_repo = create_sub.add_parser("repository", help="создать репозиторий", **common)
    create_repo.add_argument("name", nargs="?", default=None, help="имя репозитория (по умолчанию — имя папки)")
    create_repo.add_argument("--server", default=None, help="адрес сервера")
    create_repo.add_argument("--user", default=None, help="ваш ник на сервере")
    create_repo.add_argument("--force", action="store_true", help="пересоздать существующий репозиторий")
    create_repo.add_argument("--timeout", type=float, default=20.0)
    create_repo.set_defaults(func=cmd_create_repository)

    load = sub.add_parser("load", help="загрузить данные в репозиторий", **common)
    load_sub = load.add_subparsers(dest="command")
    load_data = load_sub.add_parser("data", help="загрузить данные в репозиторий", **common)
    load_data.add_argument("name", nargs="?", default=None, help="имя репозитория (по умолчанию — локальное)")
    load_data.add_argument("--server", default=None, help="адрес сервера")
    load_data.add_argument("--no-analyze", action="store_true", help="не запускать анализ на сервере")
    load_data.add_argument("--timeout", type=float, default=600.0)
    load_data.set_defaults(func=cmd_load_data)

    connection = sub.add_parser("connection", help="проверить соединение с сервером", **common)
    connection_sub = connection.add_subparsers(dest="command")
    conn_check = connection_sub.add_parser("check", help="проверить соединение с сервером", **common)
    conn_check.add_argument("--server", default=None, help="адрес сервера")
    conn_check.add_argument("--user", default=None, help="ваш ник на сервере")
    conn_check.add_argument("--timeout", type=float, default=10.0)
    conn_check.set_defaults(func=cmd_connection_check)

    help_cmd = sub.add_parser("help", help="справка", **common)
    help_cmd.set_defaults(func=cmd_help)

    version_cmd = sub.add_parser("version", help="версия cryless", **common)
    version_cmd.set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if not getattr(args, "func", None):
            raise UsageError("Не указана команда")
        return args.func(args)
    except UsageError as e:
        fail(str(e))
        return 2
    except CrylessError as e:
        fail(str(e))
        return 1
    except KeyboardInterrupt:
        fail("Прервано пользователем")
        return 130


if __name__ == "__main__":
    sys.exit(main())
