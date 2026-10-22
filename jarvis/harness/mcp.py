"""외부 도구 연결(MCP, Model Context Protocol) — OpenCode 의 mcp 처럼 설정에 적은 서버의 도구를 하네스 도구로 붙인다(ADR-112).

표준 입출력(stdio) JSON-RPC 2.0 만: 서버를 띄워 initialize → notifications/initialized → tools/list, 부를 때 tools/call.
도구 이름은 'mcp__<서버>__<도구>'. 설정은 ALEX 가 주는 목록(예: data/mcp.json — {"servers": [{"name", "command": [...],
"env": {}, "cwd": ""}]}). 권한은 규칙의 'mcp__*'(기본: 일꾼 거절, 대화 묻기). 서버가 죽거나 늦으면 'Error: …' 로 돌려준다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
from typing import Any

PROTOCOL = "2025-06-18"
TIMEOUT = 60.0


class MCPError(RuntimeError):
    pass


class MCPServer:
    def __init__(self, name: str, command: list[str], env: dict | None = None, cwd: str = "") -> None:
        self.name = re.sub(r"[^A-Za-z0-9_-]", "_", name)
        self.command, self.env, self.cwd = command, env or {}, cwd
        self.process: subprocess.Popen | None = None
        self.replies: dict[int, queue.Queue] = {}
        self.next_id = 0
        self.lock = threading.Lock()
        self.tools: list[dict] = []

    def start(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                        env={**os.environ, **self.env}, cwd=self.cwd or None, creationflags=flags)
        threading.Thread(target=self._read, daemon=True, name=f"alex-mcp-{self.name}").start()
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "ALEX", "version": "1.0"}})
        self.notify("notifications/initialized")
        self.tools = list(self.request("tools/list", {}).get("tools") or [])

    def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and isinstance(message.get("id"), int) and message["id"] in self.replies:
                self.replies[message["id"]].put(message)
        for box in list(self.replies.values()):
            box.put({"error": {"message": "server exited"}})

    def _send(self, payload: dict) -> None:
        if self.process is None or self.process.poll() is not None or self.process.stdin is None:
            raise MCPError(f"MCP server '{self.name}' is not running")
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method: str, params: dict, timeout: float = TIMEOUT) -> dict:
        with self.lock:
            self.next_id += 1
            ident = self.next_id
            box: queue.Queue = queue.Queue()
            self.replies[ident] = box
        try:
            self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
            try:
                reply = box.get(timeout=timeout)
            except queue.Empty as exc:
                raise MCPError(f"MCP server '{self.name}' did not answer {method} in {timeout:.0f}s") from exc
        finally:
            self.replies.pop(ident, None)
        if "error" in reply:
            raise MCPError(str((reply.get("error") or {}).get("message") or reply["error"]))
        return reply.get("result") or {}

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(5)
        for pipe in (self.process.stdin, self.process.stdout):
            if pipe is not None:
                pipe.close()


class MCPHub:
    """여러 MCP 서버 — specs()(하네스 도구 설명서), call(이름, 인자)."""

    def __init__(self, servers: list[MCPServer]) -> None:
        self.servers = {s.name: s for s in servers}
        self.errors: dict[str, str] = {}

    @classmethod
    def from_config(cls, path: Path) -> "MCPHub | None":
        if not Path(path).is_file():
            return None
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        servers = [MCPServer(str(item["name"]), [str(x) for x in item["command"]], item.get("env"), str(item.get("cwd") or ""))
                   for item in data.get("servers", []) if isinstance(item, dict) and item.get("enabled", True)
                   and item.get("name") and isinstance(item.get("command"), list)]
        return cls(servers) if servers else None

    def start(self) -> None:
        for name, server in self.servers.items():
            try:
                server.start()
            except (MCPError, OSError) as exc:
                self.errors[name] = str(exc)

    def specs(self) -> list[dict]:
        out = []
        for name, server in self.servers.items():
            for tool in server.tools:
                schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object", "properties": {}}
                out.append({"type": "function", "function": {
                    "name": f"mcp__{name}__{tool.get('name', '')}"[:64],
                    "description": str(tool.get("description") or f"{tool.get('name')} (MCP server {name})")[:4000],
                    "parameters": schema}})
        return out

    def call(self, full_name: str, args: dict[str, Any]) -> str:
        match = re.fullmatch(r"mcp__([^_]+(?:_[^_]+)*?)__(.+)", full_name)
        if not match or match.group(1) not in self.servers:
            return f"Error: unknown MCP tool '{full_name}'"
        server = self.servers[match.group(1)]
        try:
            result = server.request("tools/call", {"name": match.group(2), "arguments": args})
        except MCPError as exc:
            return f"Error: {exc}"
        texts = [str(item.get("text", "")) for item in result.get("content") or [] if isinstance(item, dict)
                 and item.get("type") == "text"]
        text = "\n".join(texts) or json.dumps(result.get("structuredContent") or result, ensure_ascii=False)[:20000]
        return ("Error: " + text) if result.get("isError") else text

    def close(self) -> None:
        for server in self.servers.values():
            server.close()
