#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bh_mcp：把 Brawlhalla 回放分析 + 知识库暴露成 MCP (Model Context Protocol) 服务器。

协议：stdio 传输，换行分隔 JSON-RPC 2.0（spec 2025-06-18 / 2026-07-28）。
零第三方依赖（纯标准库）：
    python bh_mcp.py

v2：工具表直接复用 agent.py 的注册表（16 个工具，全部输出给 LLM 读的
紧凑文本），与桌面端 Agent 完全同源——新增工具两边自动同步。
接入 ZCode 等 MCP 客户端：见 mcp-config-example.json。
"""
import sys
import json
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "brawlhalla-replay-lab", "version": "2.0.0"}

from agent import _tools_registry, dispatch_tool, PROVIDERS, SYSTEM_PROMPT  # noqa: E402
import memory_store  # noqa: E402

TOOLS = _tools_registry()
_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def _reply(msg_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(req):
    method = req.get("method", "")
    msg_id = req.get("id")
    if method == "initialize":
        client_ver = (req.get("params") or {}).get("protocolVersion")
        _reply(msg_id, {
            "protocolVersion": client_ver or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    elif method == "notifications/initialized":
        pass
    elif method == "ping":
        _reply(msg_id, {})
    elif method == "tools/list":
        _reply(msg_id, {"tools": [
            {"name": t["name"], "description": t["desc"],
             "inputSchema": t["params"]} for t in TOOLS]})
    elif method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = _TOOLS_BY_NAME.get(name)
        if not tool:
            _reply(msg_id, error={"code": -32602,
                                  "message": f"unknown tool: {name}"})
            return
        try:
            result = dispatch_tool(name, json.dumps(args, ensure_ascii=False))
            text = result if isinstance(result, str) else \
                json.dumps(result, ensure_ascii=False)
            _reply(msg_id, {"content": [{"type": "text", "text": text}],
                            "isError": False})
        except Exception as e:  # noqa: BLE001
            _reply(msg_id, {"content": [{"type": "text",
                                         "text": f"{type(e).__name__}: {e}"}],
                            "isError": True})
    elif msg_id is not None:
        _reply(msg_id, error={"code": -32601, "message": f"unknown method: {method}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            _reply(None, error={"code": -32700, "message": "parse error"})
            continue
        try:
            _handle(req)
        except Exception as e:  # noqa: BLE001
            if req.get("id") is not None:
                _reply(req["id"], error={"code": -32603,
                                         "message": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
