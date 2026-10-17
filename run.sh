#!/usr/bin/env bash
#
# Team Server — универсальный запуск (любой Linux-дистрибутив).
#
# Использование:
#   ./run.sh                      основной сайт + сервер (Host), http://<IP>:8000
#   ./run.sh server               то же самое
#   ./run.sh dev                  сайт в режиме разработки (Vite, порт 5173)
#   ./run.sh console [args...]    консоль обмена Host ⇄ Pi
#                                 (Pi: ./run.sh console --host <IP-хоста>)
#   ./run.sh cryless [args...]    библиотека команд cryless
#                                 (create repository / load data / connection check)
#   ./run.sh demo [args...]       тестовый режим: Host + Pi на одной машине
#                                 (--with-web — ещё и проверить основной сайт)
#   ./run.sh test                 e2e + тесты сайта + тесты cryless
#   ./setup.sh                    установка: Python + сборка сайта (web/dist)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PY=".venv/bin/python"

case "${1:-server}" in
  setup)
    shift
    exec ./setup.sh "$@"
    ;;
  server|web)
    [ -x "$PY" ] || exec ./setup.sh
    exec "$PY" run_server.py
    ;;
  dev)
    if [ ! -d web/node_modules ]; then
      echo "Нет web/node_modules — выполните: (cd web && npm install)" >&2
      exit 1
    fi
    cd web && exec npm run dev
    ;;
  console|host|pi|connect)
    [ -x "$PY" ] || exec ./setup.sh
    shift
    exec "$PY" getting_started/gettingstarted_linux.py "$@"
    ;;
  cryless)
    [ -x "$PY" ] || exec ./setup.sh
    shift
    exec "$PY" -m cryless "$@"
    ;;
  demo|test-mode|testmode)
    [ -x "$PY" ] || exec ./setup.sh
    shift
    exec "$PY" getting_started/gettingstarted_linux.py --mode test "$@"
    ;;
  test|tests)
    [ -x "$PY" ] || exec ./setup.sh --dev
    if ! "$PY" -m pytest --version >/dev/null 2>&1; then
      echo "pytest не установлен — запустите: ./setup.sh --dev" >&2
      exit 1
    fi
    "$PY" getting_started/tests/e2e_system.py
    exec "$PY" -m pytest tests/cryless tests/app_platform -q -p no:cacheprovider
    ;;
  --help|-h|help)
    sed -n '3,16p' "$0"
    ;;
  *)
    echo "Неизвестная команда: $1 (см. ./run.sh --help)" >&2
    exit 1
    ;;
esac
