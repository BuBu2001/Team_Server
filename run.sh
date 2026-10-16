#!/usr/bin/env bash
#
# Team Server — универсальный запуск (любой Linux-дистрибутив).
#
# Использование:
#   ./run.sh                      сервер веб-панели (Host), http://<IP>:8000
#   ./run.sh server               то же самое
#   ./run.sh console [args...]    консоль обмена Host ⇄ Pi
#                                 (Pi: ./run.sh console --host <IP-хоста>)
#   ./run.sh test                 e2e + веб-тесты
#   ./setup.sh --dev              установка с инструментами тестов
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
  console|host|pi|connect)
    [ -x "$PY" ] || exec ./setup.sh
    shift
    exec "$PY" getting_started/gettingstarted_linux.py "$@"
    ;;
  test|tests)
    [ -x "$PY" ] || exec ./setup.sh --dev
    if ! "$PY" -m pytest --version >/dev/null 2>&1; then
      echo "pytest не установлен — запустите: ./setup.sh --dev" >&2
      exit 1
    fi
    "$PY" getting_started/tests/e2e_system.py
    exec "$PY" -m pytest tests/app_platform -q -p no:cacheprovider
    ;;
  --help|-h|help)
    sed -n '3,13p' "$0"
    ;;
  *)
    echo "Неизвестная команда: $1 (см. ./run.sh --help)" >&2
    exit 1
    ;;
esac
