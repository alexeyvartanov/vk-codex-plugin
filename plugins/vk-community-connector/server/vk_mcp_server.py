#!/usr/bin/env python3
"""Standalone, dependency-free VK API MCP server.

Transport: MCP JSON-RPC over stdin/stdout.  No Node.js or third-party Python
packages are required.  VK's official JSON schema is used as the method catalog.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA_DIR = pathlib.Path(os.environ.get("VK_MCP_SCHEMA", ROOT / "schema"))
CONFIG_PATH = pathlib.Path(os.environ.get("VK_MCP_CONFIG", ROOT / "config" / "connections.json"))
API_VERSION = os.environ.get("VK_API_VERSION", "5.199")
API_URL = "https://api.vk.com/method/{}"


def _json(path: pathlib.Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def load_methods() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    catalog = SCHEMA_DIR / "catalog.json"
    if catalog.exists():
        for method in _json(catalog, []):
            if isinstance(method, dict) and method.get("name"):
                result[method["name"]] = method
        return result
    for path in sorted(SCHEMA_DIR.glob("*/methods.json")):
        data = _json(path, {})
        for method in data.get("methods", []):
            if method.get("name"):
                result[method["name"]] = method
    return result


METHODS = load_methods()

# Классификация по белому списку: методом чтения считается только то, что
# начинается с get/search/resolve/is/check или явно перечислено ниже.
# Всё остальное (включая execute) считается записью.
READ_PREFIXES = ("get", "search", "resolve", "is", "check")
READ_EXCEPTIONS = {
    "friends.areFriends", "leadForms.list", "market.filterCategories",
    "pages.parseWiki", "wall.parseAttachedLink", "translations.translate",
    "video.liveGetCategories", "apps.promoHasActiveGift",
}


def is_write(method: str) -> bool:
    if method in READ_EXCEPTIONS:
        return False
    if "." not in method:
        return True
    return not method.split(".", 1)[1].startswith(READ_PREFIXES)


# Что можно отправлять, когда для подключения включена запись (allow_writes).
# Остальные методы записи (посты, баны, execute и т.п.) нужно перечислить явно
# в allowed_write_methods подключения, "*" разрешает всё.
DEFAULT_WRITE_METHODS = ("messages.send", "messages.markAsRead")


def config() -> dict[str, Any]:
    data = _json(CONFIG_PATH, {})
    return data if isinstance(data, dict) else {}


def connections() -> list[dict[str, Any]]:
    return [x for x in config().get("connections", []) if isinstance(x, dict) and x.get("enabled", True)]


def get_connection(connection_id: str) -> dict[str, Any]:
    for item in connections():
        if item.get("id") == connection_id:
            return item
    raise ValueError(f"Подключение не найдено: {connection_id}")


def token_for(item: dict[str, Any]) -> str:
    path = pathlib.Path(item.get("token_file", ""))
    if not path.is_absolute():
        path = CONFIG_PATH.parent / path
    try:
        # Блокнот Windows может сохранить UTF-8 BOM в начале файла.
        token = path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise ValueError(f"Не удалось прочитать token_file: {path}") from exc
    if not token:
        raise ValueError(f"Файл токена пуст: {path}")
    return token


def _vk_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple)):
        return ",".join(_vk_value(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def api_call(method: str, params: dict[str, Any], token: str) -> dict[str, Any]:
    query = {k: _vk_value(v) for k, v in dict(params).items() if v is not None}
    query["v"] = API_VERSION
    body = urllib.parse.urlencode(query).encode("utf-8")
    request = urllib.request.Request(
        API_URL.format(method),
        data=body,
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"VK API transport error: {type(exc).__name__}") from exc
    if "error" in data:
        error = data["error"]
        if isinstance(error, dict):
            error = {k: v for k, v in error.items() if k != "request_params"}
        raise RuntimeError(json.dumps({"vk_error": error, "method": method}, ensure_ascii=False))
    return data.get("response", data)


def resolve_group_ref(ref: str, token: str) -> dict[str, Any]:
    raw = str(ref).strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        raw = urllib.parse.urlparse(raw).path.strip("/").split("/")[0]
    raw = raw.lstrip("@")
    for prefix in ("club", "public", "event"):
        if raw.startswith(prefix) and raw[len(prefix):].isdigit():
            raw = raw[len(prefix):]
    raw = raw.lstrip("-")
    result = api_call("groups.getById", {"group_ids": raw}, token)
    if isinstance(result, list):
        if not result:
            raise ValueError("VK не вернуло данные сообщества")
        return result[0]
    if isinstance(result, dict):
        # VK API can return either an array or an object depending on the
        # method/API version and the selected response shape.
        groups = result.get("groups")
        if isinstance(groups, list) and groups:
            return groups[0]
        return result
    raise ValueError("Неожиданный формат ответа groups.getById")


def method_public(method: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": method.get("name"),
        "description": method.get("description", ""),
        "access_token_type": method.get("access_token_type", []),
        "parameters": method.get("parameters", []),
        "responses": method.get("responses", {}),
        "errors": method.get("errors", []),
        "write": is_write(method.get("name", "")),
    }


def tool_result(value: Any) -> dict[str, Any]:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}], "structuredContent": value}


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "vk_list_connections":
        return tool_result([{k: v for k, v in c.items() if k != "token_file"} for c in connections()])
    if name == "vk_search_methods":
        query = str(args.get("query", "")).lower()
        limit = min(max(int(args.get("limit", 20)), 1), 100)
        found = [method_public(m) for n, m in METHODS.items() if query in n.lower() or query in str(m.get("description", "")).lower()]
        return tool_result(found[:limit])
    if name == "vk_method_info":
        method = METHODS.get(str(args.get("method", "")))
        if not method:
            raise ValueError("Метод отсутствует в официальной схеме VK API")
        return tool_result(method_public(method))
    if name == "vk_check_connection":
        item = get_connection(str(args["connection_id"]))
        token = token_for(item)
        group = resolve_group_ref(str(item["community_ref"]), token)
        return tool_result({"connection_id": item["id"], "community": group, "token_type": item.get("token_type", "unknown"), "api_version": API_VERSION})
    if name == "vk_get_capabilities":
        item = get_connection(str(args["connection_id"]))
        token = token_for(item)
        group = resolve_group_ref(str(item["community_ref"]), token)
        checks = {}
        for method in ("groups.getById", "wall.get", "wall.post", "messages.getConversations"):
            if method not in METHODS:
                continue
            if is_write(method):
                checks[method] = {"status": "not_probed", "reason": "write method"}
                continue
            try:
                params = {"group_ids": str(group["id"])} if method == "groups.getById" else {}
                if method == "wall.get":
                    params = {"owner_id": -int(group["id"]), "count": 1}
                api_call(method, params, token)
                checks[method] = {"status": "available"}
            except RuntimeError as exc:
                checks[method] = {"status": "unavailable_or_denied", "detail": str(exc)}
        return tool_result({"connection_id": item["id"], "community": group, "checks": checks})
    if name in ("vk_call_read", "vk_call"):
        method_name = str(args.get("method", ""))
        method = METHODS.get(method_name)
        if not method:
            raise ValueError("Метод отсутствует в официальной схеме VK API")
        write = is_write(method_name)
        if name == "vk_call_read" and write:
            raise ValueError("Это метод записи; используйте vk_call с confirm=true")
        if write and not args.get("confirm", False):
            raise ValueError("Операция записи требует confirm=true")
        item = get_connection(str(args["connection_id"]))
        token_type = item.get("token_type", "unknown")
        accepted = method.get("access_token_type", [])
        if accepted and token_type not in accepted and "open" not in accepted:
            raise ValueError(f"Метод {method_name} допускает типы токенов {accepted}, а подключение отмечено как {token_type}")
        if write and not item.get("allow_writes", False):
            raise ValueError("Отправка выключена для этого подключения. Включить её может навык «Подключи ВК» (режим отправки с подтверждением).")
        if write:
            allowed = item.get("allowed_write_methods") or list(DEFAULT_WRITE_METHODS)
            if "*" not in allowed and method_name not in allowed:
                raise ValueError(f"Метод {method_name} не входит в разрешённые для записи: {', '.join(allowed)}")
        params = dict(args.get("params", {}))
        if method_name == "messages.send" and "random_id" not in params:
            # ВКонтакте требует random_id, иначе отказывает или может отправить дубль.
            import secrets
            params["random_id"] = secrets.randbelow(2**31 - 1) + 1
        return tool_result(api_call(method_name, params, token_for(item)))
    raise ValueError(f"Неизвестный MCP-инструмент: {name}")


TOOLS = [
    {"name": "vk_list_connections", "description": "Список подключённых сообществ без токенов.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "vk_search_methods", "description": "Поиск методов в официальной схеме VK API.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "vk_method_info", "description": "Параметры, права и ответ метода VK API.", "inputSchema": {"type": "object", "properties": {"method": {"type": "string"}}, "required": ["method"]}},
    {"name": "vk_check_connection", "description": "Проверка токена и сообщества.", "inputSchema": {"type": "object", "properties": {"connection_id": {"type": "string"}}, "required": ["connection_id"]}},
    {"name": "vk_get_capabilities", "description": "Проверка безопасных возможностей конкретного токена.", "inputSchema": {"type": "object", "properties": {"connection_id": {"type": "string"}}, "required": ["connection_id"]}},
    {"name": "vk_call_read", "description": "Вызов метода VK API только для чтения.", "inputSchema": {"type": "object", "properties": {"connection_id": {"type": "string"}, "method": {"type": "string"}, "params": {"type": "object"}}, "required": ["connection_id", "method"]}},
    {"name": "vk_call", "description": "Универсальный вызов VK API. Запись работает, только если для подключения включена отправка, и требует confirm=true. Перед отправкой сообщения покажи пользователю текст и получатель и дождись его согласия.", "inputSchema": {"type": "object", "properties": {"connection_id": {"type": "string"}, "method": {"type": "string"}, "params": {"type": "object"}, "confirm": {"type": "boolean"}}, "required": ["connection_id", "method"]}},
]


def reply(msg_id: Any, result: Any = None, error: Any = None) -> None:
    message = {"jsonrpc": "2.0", "id": msg_id, "result": result} if error is None else {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(error)}}
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            msg_id = request.get("id")
            if method == "initialize":
                reply(msg_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "vk-api-mcp", "version": "0.1.0"}})
            elif method == "tools/list":
                reply(msg_id, {"tools": TOOLS})
            elif method == "tools/call":
                params = request.get("params", {})
                reply(msg_id, call_tool(params.get("name", ""), params.get("arguments", {})))
            elif msg_id is not None:
                reply(msg_id, {})
        except Exception as exc:
            if "msg_id" in locals():
                reply(msg_id, error=exc)


if __name__ == "__main__":
    main()
