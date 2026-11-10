"""Connect to several MCP servers, keep their tools up to date, and call them.

    hub = MCPHub(["loans", "payments", "customer", "cards", "deposits"]).start()
    hub.entries()            # every tool, ready for the router
    hub.call("get_next_emi", {"loan_id": "L1001"})
    hub.stop()

The MCP client is async; the hub runs it on a background thread so the (sync) agent and router
can use it directly. When a server says its tool list changed, the hub re-lists that server and
bumps `version`, so the router knows to re-index.
"""
import asyncio
import json
import sys
import threading
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from router.index import Entry

ROOT = Path(__file__).parent.parent
ALL_GROUPS = ["loans", "payments", "customer", "cards", "deposits"]


class HubTool:
    """What the safety layer needs to know about a tool, taken from MCP metadata."""

    def __init__(self, tool):
        meta = tool.meta or {}
        self.name, self.description = tool.name, tool.description or ""
        hints = tool.annotations
        # Unknown risk is treated as a write: it needs the caller's yes. Safe by default.
        self.risk = meta.get("risk") or ("read" if hints and hints.read_only_hint else
                                         "irreversible" if hints and hints.destructive_hint else "write")
        self.needs, self.next = list(meta.get("needs", [])), list(meta.get("next", []))
        self.examples, self.area = list(meta.get("examples", [])), meta.get("area")
        self.input_schema = tool.input_schema or {"type": "object", "properties": {}}


class MCPHub:
    def __init__(self, groups=ALL_GROUPS):
        self.groups = list(groups)
        self.tools = {}           # group -> [HubTool]
        self.sessions = {}        # group -> ClientSession
        self.version = 0
        self.changes = []         # (time, group) log of tool-list changes
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._stop = None
        self._ready = {}

    # ---------- lifecycle ----------

    def start(self, timeout=60):
        self._thread.start()
        self._run(self._start_all(), timeout)
        return self

    def stop(self):
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
            time.sleep(0.5)
        self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self, coro, timeout=30):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _start_all(self):
        self._stop = asyncio.Event()
        for g in self.groups:
            self._ready[g] = asyncio.Event()
            self._loop.create_task(self._serve(g))
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in self._ready.values())), 60)

    async def _serve(self, group):
        params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_servers.lending_server", group],
                                       cwd=str(ROOT), env={"PYTHONPATH": str(ROOT)})

        async def on_message(message):
            if isinstance(message, types.ToolListChangedNotification):
                self._loop.create_task(self._refresh(group))

        async with stdio_client(params, errlog=open("/dev/null", "w")) as (read, write):
            async with ClientSession(read, write, message_handler=on_message) as session:
                await session.initialize()
                self.sessions[group] = session
                await self._refresh(group, initial=True)
                self._ready[group].set()
                await self._stop.wait()

    async def _refresh(self, group, initial=False):
        result = await self.sessions[group].list_tools()
        self.tools[group] = [HubTool(t) for t in result.tools]
        self.version += 1
        if not initial:
            self.changes.append((time.time(), group))

    # ---------- what the router and agent use ----------

    def all_tools(self):
        return {t.name: t for g in self.groups for t in self.tools.get(g, [])}

    def entries(self):
        """Router entries for every tool on every server."""
        out = []
        for t in self.all_tools().values():
            e = Entry(t.name, t.description,
                      {n: (p.get("description", "") if isinstance(p, dict) else "")
                       for n, p in t.input_schema.get("properties", {}).items()},
                      t.examples, area=t.area)
            e.needs, e.next = t.needs, t.next
            out.append(e)
        return out

    def schemas(self, names):
        """Function-calling definitions for the model, straight from each server's input schema."""
        tools = self.all_tools()
        return [{"type": "function", "function": {"name": n, "description": tools[n].description,
                                                  "parameters": tools[n].input_schema}}
                for n in names if n in tools]

    def server_of(self, name):
        return next((g for g in self.groups if any(t.name == name for t in self.tools.get(g, []))), None)

    def call(self, name, arguments):
        """Run a tool on whichever server owns it. Returns the same shape as agent.executor.execute."""
        start = time.time()
        group = self.server_of(name)
        if group is None:
            return {"ok": False, "error": "unknown_tool", "message": f"No server has a tool called {name}",
                    "latency_ms": 0}
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except json.JSONDecodeError as e:
            return {"ok": False, "error": "bad_json", "message": str(e), "latency_ms": 0}
        try:
            result = self._run(self.sessions[group].call_tool(name, args))
        except Exception as e:
            return {"ok": False, "error": "server_error", "message": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000)}
        text = "".join(c.text for c in result.content if isinstance(c, types.TextContent))
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {"text": text}
        ms = int((time.time() - start) * 1000)
        if result.is_error:
            return {"ok": False, "error": body.get("error", "tool_error"), "message": body.get("message", text),
                    "latency_ms": ms}
        return {"ok": True, "result": body, "latency_ms": ms}


def routed_selector(hub, config):
    """A select_tools(messages) function for the agent that re-indexes when a server's tools change."""
    from router.router import Router

    state = {"version": None, "router": None}

    def current_router():
        if state["version"] != hub.version:
            state["router"] = Router(config, entries=hub.entries())
            state["version"] = hub.version
        return state["router"]

    def select_tools(messages):
        return current_router().for_agent()(messages)

    select_tools.router = current_router
    return select_tools
