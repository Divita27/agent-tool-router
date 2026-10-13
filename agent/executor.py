"""Runs a tool call from the model against the lending backend.

Two modes:
    local  calls the handler functions directly (fast, used for benchmarks)
    http   calls the running API, so the failure switch works (start it with uvicorn first)
"""
import inspect
import json
import time

import requests

from backend.handlers import HANDLERS, ToolError

API_URL = "http://127.0.0.1:8000"


def execute(name, arguments, mode="local", timeout=8):
    """Run one tool. `arguments` is the JSON string from the model.

    Always returns a dict with "ok" (bool), plus "result" or "error" and "message",
    and "latency_ms". Never raises, so one bad tool call can't crash a conversation.
    """
    start = time.time()
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        return _done(start, ok=False, error="bad_json", message=f"Could not read the tool arguments: {e}")

    if name not in HANDLERS:
        return _done(start, ok=False, error="unknown_tool", message=f"There is no tool called {name}")

    if mode == "http":
        return _http(name, args, start, timeout)

    fn = HANDLERS[name]
    try:
        inspect.signature(fn).bind(**args)
    except TypeError as e:
        return _done(start, ok=False, error="invalid_arguments", message=str(e))
    try:
        return _done(start, ok=True, result=fn(**args))
    except ToolError as e:
        return _done(start, ok=False, error=e.code, message=e.message)
    except Exception as e:  # a bug in the backend: report it, don't crash the call
        return _done(start, ok=False, error="internal_error", message=f"{type(e).__name__}: {e}")


def _http(name, args, start, timeout):
    try:
        r = requests.post(f"{API_URL}/tools/{name}", json=args, timeout=timeout)
    except requests.Timeout:
        return _done(start, ok=False, error="timeout", message="The service took too long to respond")
    except requests.ConnectionError:
        return _done(start, ok=False, error="service_down", message="The service could not be reached")
    body = r.json()
    if r.status_code == 200:
        return _done(start, ok=True, result=body)
    detail = body.get("detail", body)
    return _done(start, ok=False, error=detail.get("error", f"http_{r.status_code}"),
                 message=detail.get("message", str(detail)))


def _done(start, **fields):
    fields["latency_ms"] = int((time.time() - start) * 1000)
    return fields
