"""Check that every tool definition matches its backend handler, then export tools/tools.json.

Run from the project folder:  python -m scripts.check_tools
"""
import inspect
import json
from collections import Counter
from pathlib import Path

from backend.handlers import HANDLERS
from tools import RISKS, TOOLS, TOOLS_BY_NAME, schema

problems = []

# 1. Same set of names on both sides
if len(TOOLS_BY_NAME) != len(TOOLS):
    dupes = [n for n, c in Counter(t.name for t in TOOLS).items() if c > 1]
    problems.append(f"Duplicate tool names: {dupes}")
for name in sorted(set(HANDLERS) - set(TOOLS_BY_NAME)):
    problems.append(f"Handler has no tool definition: {name}")
for name in sorted(set(TOOLS_BY_NAME) - set(HANDLERS)):
    problems.append(f"Tool definition has no handler: {name}")

# 2. Parameters match the handler's signature exactly
for tool in TOOLS:
    fn = HANDLERS.get(tool.name)
    if not fn:
        continue
    sig = inspect.signature(fn).parameters
    if set(sig) != set(tool.params):
        problems.append(f"{tool.name}: handler params {sorted(sig)} != tool params {sorted(tool.params)}")
        continue
    for pname, p in tool.params.items():
        handler_required = sig[pname].default is inspect.Parameter.empty
        if handler_required != p.required:
            problems.append(f"{tool.name}.{pname}: required={p.required} but handler says {handler_required}")
        if p.type not in ("string", "number", "integer"):
            problems.append(f"{tool.name}.{pname}: unknown type {p.type}")

# 3. Metadata is complete
for tool in TOOLS:
    if tool.risk not in RISKS:
        problems.append(f"{tool.name}: unknown risk {tool.risk}")
    if len(tool.examples) < 3:
        problems.append(f"{tool.name}: needs at least 3 examples")
    if not tool.description.endswith("."):
        problems.append(f"{tool.name}: description should be a full sentence")

if problems:
    print(f"{len(problems)} problem(s):")
    for p in problems:
        print("  -", p)
    raise SystemExit(1)

# Export for inspection and for the README
out = Path(__file__).parent.parent / "tools" / "tools.json"
out.write_text(json.dumps(
    [{"name": t.name, "area": t.area, "risk": t.risk, "examples": t.examples, "schema": schema(t)} for t in TOOLS],
    ensure_ascii=False, indent=2))

print(f"OK: {len(TOOLS)} tools match their handlers exactly.\n")
print("By area:", dict(sorted(Counter(t.area for t in TOOLS).items())))
print("By risk:", dict(Counter(t.risk for t in TOOLS)))
chars = len(json.dumps([schema(t) for t in TOOLS]))
print(f"\nAll schemas together: {chars:,} characters (exact token count comes from the API in Step 6)")
print(f"Exported {out.relative_to(Path.cwd())}")
