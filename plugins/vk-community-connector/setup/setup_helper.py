"""Помощник установщика VK MCP: проверка ключа, connections.json, config.toml.

Запуск: python setup_helper.py <папка_установки> <ссылка_на_сообщество> [файл_ключа]
Ключ читается из локального файла UTF-8/UTF-8-BOM и не передаётся в командной
строке или сообщении Codex.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

VK_ERRORS = {
    5: "ключ не подходит: скопируйте его заново целиком",
    15: "у ключа нет доступа к этому сообществу",
    27: "это ключ сообщества, а метод требует ключ пользователя",
    28: "ключ приложения здесь не подходит",
    100: "сообщество не найдено: проверьте ссылку",
}


def say(text: str = "") -> None:
    print(text, flush=True)


def fail(text: str) -> None:
    say("")
    say("ОШИБКА: " + text)
    sys.exit(2)


def load_server(root: pathlib.Path):
    os.environ["VK_MCP_CONFIG"] = str(root / "config" / "connections.json")
    os.environ["VK_MCP_SCHEMA"] = str(root / "schema")
    spec = importlib.util.spec_from_file_location("vk_mcp_server", root / "src" / "vk_mcp_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def explain(exc: Exception) -> str:
    text = str(exc)
    try:
        code = json.loads(text)["vk_error"]["error_code"]
        return VK_ERRORS.get(code, f"ВКонтакте ответил ошибкой {code}")
    except (ValueError, KeyError, TypeError):
        pass
    if "transport" in text:
        return "нет связи с ВКонтакте: проверьте интернет, а если включён VPN, выключите его и запустите снова"
    return text


def connection_id(group: dict) -> str:
    name = str(group.get("screen_name") or "").lower()
    name = re.sub(r"[^a-z0-9_-]", "", name)
    return name or f"club{group.get('id')}"


def update_connections(root: pathlib.Path, conn: dict) -> None:
    path = root / "config" / "connections.json"
    data = {"connections": []}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError:
            shutil.copy2(path, path.with_suffix(".json.broken"))
            data = {"connections": []}
    items = [c for c in data.get("connections", []) if isinstance(c, dict) and c.get("id") != conn["id"]]
    items.append(conn)
    data["connections"] = items
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


VK_TABLE = re.compile(r"^\s*\[\s*mcp_servers\s*\.\s*(vk|\"vk\")\s*(\.[^\]]*)?\]\s*(#.*)?$")
ANY_TABLE = re.compile(r"^\s*\[")


def update_codex_config(python_exe: str, server: pathlib.Path) -> pathlib.Path:
    home = os.environ.get("CODEX_HOME") or str(pathlib.Path.home() / ".codex")
    cfg = pathlib.Path(home) / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    old = cfg.read_text(encoding="utf-8-sig") if cfg.exists() else ""
    if cfg.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(cfg, cfg.with_name(f"config.toml.bak-{stamp}"))

    kept, skipping = [], False
    for line in old.splitlines():
        if VK_TABLE.match(line):
            skipping = True
            continue
        if skipping and ANY_TABLE.match(line):
            skipping = False
        if not skipping:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()

    block = [
        "[mcp_servers.vk]",
        "command = " + json.dumps(python_exe, ensure_ascii=False),
        "args = [" + json.dumps(str(server), ensure_ascii=False) + "]",
        "startup_timeout_sec = 30",
    ]
    new = "\n".join(kept + ([""] if kept else []) + block) + "\n"

    try:
        import tomllib  # Python 3.11+
        parsed = tomllib.loads(new)
        if parsed["mcp_servers"]["vk"]["command"] != python_exe:
            raise ValueError("vk block mismatch")
    except ModuleNotFoundError:
        pass
    except Exception as exc:  # битый файл настроек: ничего не меняем
        fail(f"файл настроек Codex не удалось разобрать ({type(exc).__name__}). Он не изменён: {cfg}")

    cfg.write_text(new, encoding="utf-8")
    return cfg


def main() -> None:
    root = pathlib.Path(sys.argv[1]).resolve()
    ref = sys.argv[2].strip()
    token_file = pathlib.Path(sys.argv[3]).expanduser() if len(sys.argv) > 3 else None
    if token_file:
        try:
            token = token_file.read_text(encoding="utf-8-sig").strip().strip('"').strip("'")
        except OSError as exc:
            fail(f"не удалось прочитать локальный файл ключа ({type(exc).__name__})")
    else:
        token = os.environ.pop("VK_INSTALL_TOKEN_VALUE", "").strip().strip('"').strip("'")
    if not token:
        fail("ключ не получен")

    server = load_server(root)
    say(f"Каталог VK API: {len(server.METHODS)} методов.")
    if not server.METHODS:
        fail("каталог методов не найден")

    if not os.environ.get("VK_INSTALL_TEST"):
        try:
            group = server.resolve_group_ref(ref, token)
        except Exception as exc:
            fail(explain(exc))
        # Ключ сообщества без параметров возвращает своё сообщество.
        try:
            own = server.api_call("groups.getById", {}, token)
            if isinstance(own, dict):
                own = own.get("groups") or [own]
            own_id = int(own[0]["id"])
        except Exception:
            fail("похоже, это не ключ сообщества. Создайте ключ в разделе «Управление» → «Работа с API» → «Ключи доступа»")
        if own_id != int(group.get("id", 0)):
            fail("ключ выдан другому сообществу, а не тому, на которое дана ссылка")
    else:
        digits = re.sub(r"\D", "", ref)
        group = {"id": int(digits or 1), "name": "Тестовое сообщество", "screen_name": "test_" + (digits or "x")}
    name = group.get("name") or ref
    say(f"Сообщество найдено: {name}")

    cid = connection_id(group)
    token_path = root / "config" / "tokens" / f"{cid}.txt"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass
    update_connections(root, {
        "id": cid,
        "name": name,
        "community_ref": str(group.get("id") or ref),
        "token_file": f"tokens/{cid}.txt",
        "token_type": "group",
        "allow_writes": False,
        "enabled": True,
    })
    say("Подключение записано. Запись в ВК выключена, только чтение.")

    if not os.environ.get("VK_INSTALL_TEST"):
        server = load_server(root)
        try:
            server.call_tool("vk_check_connection", {"connection_id": cid})
        except Exception as exc:
            fail("проверка через vk_check_connection не прошла: " + explain(exc))
        say("Проверка vk_check_connection прошла.")

    cfg = update_codex_config(sys.executable, root / "src" / "vk_mcp_server.py")
    say(f"Codex настроен: {cfg}")
    say(f"Имя подключения для Codex: {cid}")


if __name__ == "__main__":
    main()
