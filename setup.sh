#!/usr/bin/env bash
#
# Team Server — универсальная установка на любом Linux-дистрибутиве.
#
# Определяет пакетный менеджер (apt/dnf/yum/pacman/zypper/apk/xbps/emerge/eopkg/pkg),
# при нехватке python3/venv/pip/git предлагает установить недостающие пакеты
# (с вашего подтверждения), затем создаёт .venv и ставит зависимости.
#
# Использование:  ./setup.sh          — рабочие зависимости
#                 ./setup.sh --dev    — + pytest и инструменты тестов
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

DEV=0
for a in "$@"; do
  case "$a" in
    --dev) DEV=1 ;;
    --help|-h) sed -n '2,12p' "$0"; exit 0 ;;
  esac
done

MIN_MAJ=3
MIN_MIN=10
VENV=".venv"
PYV="$VENV/bin/python"

C_C=$'\033[1;36m'; C_G=$'\033[1;32m'; C_Y=$'\033[1;33m'; C_R=$'\033[1;31m'; C_0=$'\033[0m'
log()  { printf '\n%s==>%s %s\n' "$C_C" "$C_0" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_Y" "$C_0" "$*"; }
die()  { printf '  %s×%s %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# --- дистрибутив и пакетный менеджер -----------------------------------------
PM=""
for c in apt-get dnf yum pacman zypper apk xbps-install emerge eopkg pkg; do
  have "$c" && { PM="$c"; break; }
done
OS_ID=$(. /etc/os-release 2>/dev/null && echo "${ID:-unknown}" || echo unknown)
SUDO=""
[ "$(id -u)" -eq 0 ] || { have sudo && SUDO="sudo"; }

sys_pkgs() {  # набор пакетов для текущего менеджера
  case "$PM" in
    apt-get)      echo "python3 python3-venv python3-pip git" ;;
    dnf|yum)      echo "python3 python3-pip git" ;;
    pacman)       echo "python python-pip git" ;;
    zypper)       echo "python3 python3-pip git" ;;
    apk)          echo "python3 py3-pip git bash" ;;
    xbps-install) echo "python3 python3-pip git" ;;
    emerge)       echo "dev-lang/python dev-vcs/git" ;;
    eopkg)        echo "python3 python-pip git" ;;
    pkg)          echo "python git" ;;  # Termux
    *)            echo "" ;;
  esac
}

install_cmd() {  # команда установки для текущего менеджера
  case "$PM" in
    apt-get)      echo "$SUDO apt-get update && $SUDO apt-get install -y" ;;
    dnf)          echo "$SUDO dnf install -y" ;;
    yum)          echo "$SUDO yum install -y" ;;
    pacman)       echo "$SUDO pacman -S --needed --noconfirm" ;;
    zypper)       echo "$SUDO zypper --non-interactive install" ;;
    apk)          echo "$SUDO apk add" ;;
    xbps-install) echo "$SUDO xbps-install -Sy" ;;
    emerge)       echo "$SUDO emerge" ;;
    eopkg)        echo "$SUDO eopkg it -y" ;;
    pkg)          echo "pkg install -y" ;;
    *)            return 1 ;;
  esac
}

ask_system() {  # предложить установку недостающих системных пакетов
  local pkgs cmd ans
  pkgs=$(sys_pkgs)
  if [ -z "$pkgs" ] || [ -z "$(install_cmd)" ]; then
    warn "Пакетный менеджер не распознан (дистрибутив: $OS_ID)."
    warn "Установите вручную: python3 >= ${MIN_MAJ}.${MIN_MIN}, python3-venv/pip, git — и повторите ./setup.sh"
    return 1
  fi
  cmd="$(install_cmd) $pkgs"
  warn "Не хватает системных пакетов: $pkgs"
  printf '    %s\n' "$cmd"
  if [ ! -t 0 ]; then
    warn "Нет интерактивного терминала — установите вручную и повторите ./setup.sh"
    return 1
  fi
  read -r -p "    Установить сейчас? [y/N] " ans
  case "$ans" in y|Y|д|Д|да|Да|yes|Yes) ;; *) return 1 ;; esac
  eval "$cmd" || die "Не удалось установить системные пакеты"
  ok "Системные пакеты установлены"
}

py_ok() {
  "$1" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= ($MIN_MAJ, $MIN_MIN) else 1)" 2>/dev/null
}

pick_python() {  # первый доступный python нужной версии
  local c
  for c in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
    have "$c" && py_ok "$c" && { echo "$c"; return 0; }
  done
  return 1
}

# --- проверки и установка ----------------------------------------------------
log "Проверка системных требаний (дистрибутив: $OS_ID, менеджер: ${PM:-не распознан})"

PYBIN=$(pick_python || true)
if [ -z "$PYBIN" ]; then
  if have python3; then
    warn "Найден $(python3 -V 2>&1 || echo python3), но нужен Python >= ${MIN_MAJ}.${MIN_MIN}"
  else
    warn "python3 не найден"
  fi
  ask_system || die "Python >= ${MIN_MAJ}.${MIN_MIN} обязателен — установите его и повторите ./setup.sh"
  PYBIN=$(pick_python) || die "Python >= ${MIN_MAJ}.${MIN_MIN} так и не найден"
fi
ok "Python: $PYBIN ($("$PYBIN" -V 2>&1))"

have git || ask_system || warn "git не найден — клонирование репозиториев по URL работать не будет"

if ! "$PYBIN" -c 'import venv' 2>/dev/null; then
  warn "У python нет модуля venv (обычно пакет python3-venv)"
  ask_system || die "Модуль venv недоступен — установите python3-venv и повторите"
fi
ok "Модуль venv доступен"

log "Виртуальное окружение ($VENV)"
if [ ! -x "$PYV" ]; then
  "$PYBIN" -m venv "$VENV" || die "Не удалось создать venv"
  ok "Создано"
else
  ok "Уже существует (переиспользую)"
fi

if ! "$PYV" -m pip --version >/dev/null 2>&1; then
  warn "В venv нет pip — пробую ensurepip"
  "$PYV" -m ensurepip --upgrade >/dev/null 2>&1 || ask_system || die "pip недоступен в venv; установите python3-pip и повторите"
fi
"$PYV" -m pip --version >/dev/null 2>&1 || die "pip по-прежнему недоступен"
ok "$("$PYV" -m pip --version 2>/dev/null | head -1)"

log "Зависимости Python (pip)"
"$PYV" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1 || warn "Не удалось обновить pip — продолжаю текущей версией"
REQ=requirements.txt
[ "$DEV" = 1 ] && REQ=requirements-dev.txt
if ! "$PYV" -m pip install -r "$REQ"; then
  die "pip не смог установить пакеты. Если ошибка про сборку (Pillow, lxml и т.п.) — установите компилятор и заголовки: apt install build-essential / pacman -S base-devel / dnf groupinstall 'Development Tools' / apk add build-base, затем повторите ./setup.sh"
fi
ok "Установлено из $REQ"

log "Готово ✔"
printf '  Сервер (Host):   ./run.sh\n'
printf '  Консоль (Pi):    ./run.sh console --host <IP-хоста>\n'
printf '  cryless (Pi):    ./run.sh cryless help\n'
printf '  Тесты:           ./run.sh test\n'
