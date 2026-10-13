"""HTTP API for the fake lending company.

Start it from the project folder:
    uvicorn backend.api:app --reload
Then open http://127.0.0.1:8000/docs to try every tool in the browser.

Every tool is   POST /tools/<tool_name>   with its arguments as a JSON body.
"""
import inspect
import random
import time

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from backend import seed
from backend.handlers import HANDLERS, ToolError

app = FastAPI(title="Demo Lending Co. API", description="Fake lending company backend for the loan agent router.")

# Failure switch: {tool_name or "*": {"mode": "error" | "timeout" | "slow", "rate": 0..1}}
FAILURES = {}
TIMEOUT_SECONDS = 10
SLOW_SECONDS = 3


def maybe_fail(tool_name):
    rule = FAILURES.get(tool_name) or FAILURES.get("*")
    if not rule or random.random() >= rule.get("rate", 1.0):
        return
    if rule["mode"] == "error":
        raise HTTPException(503, {"error": "service_unavailable", "message": "The service is temporarily unavailable"})
    if rule["mode"] == "timeout":
        time.sleep(TIMEOUT_SECONDS)
        raise HTTPException(504, {"error": "timeout", "message": "The service took too long to respond"})
    if rule["mode"] == "slow":
        time.sleep(SLOW_SECONDS)


def make_endpoint(name, fn):
    signature = inspect.signature(fn)

    def endpoint(args: dict = Body(default={})):
        maybe_fail(name)
        try:
            signature.bind(**args)
        except TypeError as e:
            return JSONResponse(status_code=400, content={"error": "invalid_arguments", "message": str(e)})
        try:
            return fn(**args)
        except ToolError as e:
            return JSONResponse(status_code=422, content={"error": e.code, "message": e.message})

    endpoint.__name__ = name
    endpoint.__doc__ = fn.__doc__
    return endpoint


for _name, _fn in HANDLERS.items():
    app.add_api_route(f"/tools/{_name}", make_endpoint(_name, _fn), methods=["POST"], tags=["tools"])


@app.get("/tools", tags=["meta"])
def list_tools():
    return {"count": len(HANDLERS), "tools": {name: list(inspect.signature(fn).parameters)
                                              for name, fn in HANDLERS.items()}}


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.get("/admin/failures", tags=["admin"])
def get_failures():
    return FAILURES


@app.post("/admin/failures", tags=["admin"])
def set_failure(tool: str = Body(...), mode: str = Body(...), rate: float = Body(1.0)):
    """Make a tool fail. tool: a tool name or "*" for all. mode: error | timeout | slow | off."""
    if tool != "*" and tool not in HANDLERS:
        raise HTTPException(404, f"Unknown tool {tool}")
    if mode == "off":
        FAILURES.pop(tool, None)
    elif mode in ("error", "timeout", "slow"):
        FAILURES[tool] = {"mode": mode, "rate": rate}
    else:
        raise HTTPException(400, "mode must be error, timeout, slow or off")
    return FAILURES


@app.post("/admin/reset", tags=["admin"])
def reset():
    """Rebuild the database from scratch and clear all failure rules."""
    FAILURES.clear()
    seed.main()
    return {"status": "reset"}
