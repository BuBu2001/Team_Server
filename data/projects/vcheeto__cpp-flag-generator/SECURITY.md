# Безопасность / Security

## Поддерживаемые версии / Supported versions

Активно разрабатываемый проект без стабильных релизов. Следите за разделом
[Releases](https://github.com/BuBu2001/C-flag-generator/releases); поддерживаются
последние две версии. / Actively developed project with no stable releases yet.
Watch the [Releases](https://github.com/BuBu2001/C-flag-generator/releases) section;
the latest two versions are supported.

## Что попадает в scope / In scope

- Уязвимости обработки входных данных: `config/flags.json`, CLI-аргументы
  (`--seed`, `--width`, `--height`, `--output`).
- Аварийные завершения, переполнения, падения (segfault) при генерации/рендере.
- Уязвимости экспортируемых файлов (PNG/SVG/PDF) при обработке сторонними
  программами.
- Problems with input handling (`config/flags.json`, CLI arguments), crashes or
  overflows during generation/rendering, and issues in exported PNG/SVG/PDF
  files.

## Как сообщить / How to report

**Не создавайте публичный issue для проблем, которые могут раскрыть
данные.** Пришлите приватное сообщение через
[GitHub Security Advisories](https://github.com/BuBu2001/C-flag-generator/security/advisories/new)
или на почту владельца репозитория (указана в профиле). / **Do not open a
public issue for anything that may expose data.** Report privately via
[GitHub Security Advisories](https://github.com/BuBu2001/C-flag-generator/security/advisories/new)
or the repository owner's email (see profile).

В отчёте укажите: / Please include:

1. Версию/коммит (commit hash или дату).
2. Команду или входные данные, воспроизводящие проблему.
3. Ожидаемое и фактическое поведение.
4. Важность (crash / корректность вывода / иное).

1. Version or commit (hash or date).
2. A command or input that reproduces the issue.
3. Expected vs. actual behavior.
4. Severity (crash / wrong output / other).

## Ответ / Response

Стараемся отвечать в течение 5 рабочих дней и выпускать фикс до публичного
описания. / We aim to respond within 5 business days and to ship a fix before
public disclosure.
