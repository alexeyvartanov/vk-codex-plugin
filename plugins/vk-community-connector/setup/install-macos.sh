#!/bin/bash
# Подключение сообщества ВКонтакте к Codex на macOS.
# Запуск: install-macos.sh prepare "<ссылка>"  затем  install-macos.sh finish "<ссылка>"
set -u

STAGE="${1:-}"
REF="${2:-}"
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$HOME/vk-mcp"
CONFIG_DIR="$ROOT/config"
TOKEN_DIR="$CONFIG_DIR/tokens"
TOKEN_FILE="$TOKEN_DIR/pending.txt"
PENDING_FILE="$CONFIG_DIR/pending.ref"

stop() {
  echo ""
  echo "ОШИБКА: $1"
  echo "Пришлите только текст ошибки, без ключа."
  exit 2
}

find_python() {
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' >/dev/null 2>&1; then
      "$c" -c 'import sys; print(sys.executable)'
      return 0
    fi
  done
  return 1
}

[ "$STAGE" = "prepare" ] || [ "$STAGE" = "finish" ] || stop "первый параметр должен быть prepare или finish"
REF="$(echo "$REF" | tr -d '[:space:]')"
[ -n "$REF" ] || stop "не указана ссылка или ID сообщества"

PY="$(find_python)" || stop "Python 3 не найден. Выполните в Терминале: xcode-select --install, дождитесь окончания установки и повторите"
echo "Python: $PY"

if [ "$STAGE" = "prepare" ]; then
  mkdir -p "$ROOT/src" "$ROOT/schema" "$TOKEN_DIR" || stop "не удалось создать папку $ROOT"
  chmod 700 "$CONFIG_DIR" "$TOKEN_DIR"
  cp "$PLUGIN_ROOT/server/vk_mcp_server.py" "$ROOT/src/" || stop "не найден файл сервера в плагине"
  cp "$PLUGIN_ROOT/schema/catalog.json" "$ROOT/schema/" || stop "не найден каталог методов в плагине"
  cp "$PLUGIN_ROOT/setup/setup_helper.py" "$ROOT/" || stop "не найден помощник установки в плагине"
  [ -f "$CONFIG_DIR/connections.json" ] || printf '{"connections":[]}\n' > "$CONFIG_DIR/connections.json"
  [ -f "$TOKEN_FILE" ] || : > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  printf '%s' "$REF" > "$PENDING_FILE"
  if [ -z "${VK_INSTALL_NO_EDITOR:-}" ] && command -v open >/dev/null 2>&1; then
    open -e "$TOKEN_FILE"
  fi
  echo ""
  echo "Открылся файл ключа в TextEdit: $TOKEN_FILE"
  echo "Вставьте ключ, сохраните файл (Cmd+S), закройте TextEdit и напишите в Codex: «готово»."
  exit 0
fi

[ -f "$PENDING_FILE" ] || stop "сначала нужно выполнить этап prepare"
[ -f "$ROOT/setup_helper.py" ] || stop "файлы установки не найдены: повторите этап prepare"
[ -f "$TOKEN_FILE" ] || stop "файл ключа не найден"
[ -n "$(tr -d '[:space:]' < "$TOKEN_FILE")" ] || stop "файл ключа пуст: вставьте ключ и сохраните его"

PYTHONIOENCODING=utf-8 "$PY" "$ROOT/setup_helper.py" "$ROOT" "$REF" "$TOKEN_FILE" || stop "проверка VK или настройка Codex не завершилась (причина выше)"

: > "$TOKEN_FILE"
rm -f "$TOKEN_FILE" "$PENDING_FILE"
echo ""
echo "ГОТОВО."
echo "Полностью закройте Codex (Cmd+Q) и откройте его снова."
echo "После перезапуска напишите: Проверь подключение к ВК."
