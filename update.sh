#!/usr/bin/env bash
# update.sh — автообновление скачанной копии проекта с GitHub.
#
# Клади этот файл в папку скачанной (клонированной) копии и запускай:
#   ./update.sh            — обновиться один раз и выйти
#   ./update.sh --watch    — следить за обновлениями автоматически
#                            (проверка каждые 30 секунд; меняется через INTERVAL)
#   INTERVAL=10 ./update.sh --watch   — проверка каждые 10 секунд
#
# Если появилась новая версия: скачиваются файлы, локальные правки аккуратно
# откладываются в stash, веб-интерфейс пересобирается (если web/node_modules
# на месте). Перезапуск сервера — вручную.
set -euo pipefail
cd "$(dirname "$0")"

BRANCH="${BRANCH:-main}"
INTERVAL="${INTERVAL:-30}"

if [ ! -d .git ]; then
    echo "ОШИБКА: это не git-клон. Скачай проект командой:"
    echo "  git clone https://github.com/BuBu2001/Team_Server.git"
    exit 1
fi

rebuild_web() {
    # Веб-интерфейс собран в web/dist — после обновления исходников пересобираем.
    if [ -d web/node_modules ]; then
        if (cd web && npm run build --silent); then
            echo "  Веб-интерфейс пересобран."
        else
            echo "  ВНИМАНИЕ: сборка веб-интерфейса не удалась — проверь вручную (cd web && npm run build)."
        fi
    else
        echo "  web/node_modules нет — веб-интерфейс не пересобирался."
    fi
}

update_once() {
    local before after
    before="$(git rev-parse HEAD)"
    git fetch --quiet origin "$BRANCH"
    after="$(git rev-parse "origin/$BRANCH")"
    if [ "$before" = "$after" ]; then
        return 1  # обновлений нет
    fi
    # локальные правки мешают обновлению — откладываем их в stash
    git stash --include-untracked --quiet 2>/dev/null || true
    git checkout --quiet "$BRANCH" 2>/dev/null || true
    git reset --hard --quiet "origin/$BRANCH"
    echo "[$(date '+%F %T')] Обновлено до: $(git log --oneline -1)"
    rebuild_web
    echo "  Не забудь перезапустить сервер, если он здесь запущен."
    return 0
}

if [ "${1:-}" = "--watch" ]; then
    echo "Слежу за обновлениями (каждые ${INTERVAL} сек). Ctrl+C — остановить."
    while true; do
        update_once || true
        sleep "$INTERVAL"
    done
else
    if update_once; then
        echo "Готово."
    else
        echo "Обновлений нет — копия актуальна ($(git log --oneline -1))."
    fi
fi
