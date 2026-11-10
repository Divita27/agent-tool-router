"""One MCP server per product group of the fake lending company.

    python -m mcp_servers.lending_server cards      # serves the 12 card tools over stdio

Each tool carries MCP's own safety hints (read-only, destructive) and, in `_meta`, what the
router and safety layer need: example phrases, risk level, area, and linked tools.

Tools can be added while the server runs: drop a JSON list into mcp_servers/extra/<group>.json.
The server notices within half a second and tells connected clients the tool list changed.
"""
import asyncio
import json
import sys
from pathlib import Path

import mcp.server.stdio
from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server

from agent.executor import execute
from tools import TOOLS, schema

GROUPS = {
    "loans": ["identity", "loans", "personal_loan", "home_loan", "vehicle_loan"],
    "payments": ["payments", "collections"],
    "customer": ["new_loans", "kyc_profile", "support"],
    "cards": ["cards"],
    "deposits": ["deposits"],
}
EXTRA_DIR = Path(__file__).parent / "extra"


def catalog_tools(group):
    out = []
    for t in TOOLS:
        if t.area in GROUPS[group]:
            out.append(types.Tool(
                name=t.name, description=t.description, input_schema=schema(t)["function"]["parameters"],
                annotations=types.ToolAnnotations(read_only_hint=t.risk == "read",
                                                  destructive_hint=t.risk == "irreversible"),
                meta={"examples": t.examples, "risk": t.risk, "area": t.area, "needs": t.needs, "next": t.next}))
    return out


def extra_tools(group):
    """Tools added at runtime. Each: name, description, params {name: description}, examples, risk, response."""
    path = EXTRA_DIR / f"{group}.json"
    if not path.exists():
        return [], {}
    try:
        items = json.loads(path.read_text())
    except json.JSONDecodeError:
        return [], {}
    tools, responses = [], {}
    for x in items:
        props = {p: {"type": "string", "description": d} for p, d in x.get("params", {}).items()}
        meta = {"examples": x.get("examples", []), "area": group, "needs": [], "next": []}
        if x.get("risk"):
            meta["risk"] = x["risk"]
        tools.append(types.Tool(name=x["name"], description=x.get("description", ""),
                                input_schema={"type": "object", "properties": props, "required": list(props)},
                                meta=meta))
        responses[x["name"]] = x.get("response", {"status": "ok"})
    return tools, responses


def make_server(group):
    state = {"tools": catalog_tools(group), "extra": [], "responses": {}, "sessions": []}
    state["extra"], state["responses"] = extra_tools(group)

    async def list_tools(ctx, params):
        if ctx.session not in state["sessions"]:
            state["sessions"].append(ctx.session)
        return types.ListToolsResult(tools=state["tools"] + state["extra"])

    async def call_tool(ctx, params):
        name, args = params.name, params.arguments or {}
        if name in state["responses"]:
            result = {"ok": True, "result": state["responses"][name]}
        else:
            result = execute(name, args)
        body = result["result"] if result["ok"] else {"error": result["error"], "message": result["message"]}
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
                                    is_error=not result["ok"])

    async def watch_extra():
        path = EXTRA_DIR / f"{group}.json"
        last = path.stat().st_mtime_ns if path.exists() else None
        while True:
            await asyncio.sleep(0.5)
            now = path.stat().st_mtime_ns if path.exists() else None
            if now != last:
                last = now
                state["extra"], state["responses"] = extra_tools(group)
                for session in list(state["sessions"]):
                    try:
                        await session.send_tool_list_changed()
                    except Exception:
                        state["sessions"].remove(session)

    server = Server(f"lending-{group}", on_list_tools=list_tools, on_call_tool=call_tool)
    return server, watch_extra


async def main(group):
    server, watch = make_server(group)
    async with mcp.server.stdio.stdio_server() as (read, write):
        watcher = asyncio.get_running_loop().create_task(watch())
        try:
            await server.run(read, write, server.create_initialization_options(NotificationOptions(tools_changed=True)))
        finally:
            watcher.cancel()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in GROUPS:
        sys.exit(f"usage: python -m mcp_servers.lending_server {{{'|'.join(GROUPS)}}}")
    asyncio.run(main(sys.argv[1]))
