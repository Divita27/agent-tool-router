"""Check the test scenarios are consistent with the tools and the backend data.

Run from the project folder:  python -m scripts.check_scenarios

For every turn with a full set of labelled arguments, it actually runs the first
matching tool against a fresh database, to prove the labelled answer works.
"""
import contextlib
import io
from collections import Counter

from backend import seed
from backend.handlers import HANDLERS, ToolError
from eval.scenarios import PERSONAS, SCENARIOS
from tools import TOOLS_BY_NAME

problems, ran = [], 0

with contextlib.redirect_stdout(io.StringIO()):
    seed.main()

for s in SCENARIOS:
    if s.customer not in PERSONAS:
        problems.append(f"{s.id}: unknown customer {s.customer}")
    for i, t in enumerate(s.turns, 1):
        where = f"{s.id} turn {i}"
        if t.outcome not in ("call", "clarify", "either"):
            problems.append(f"{where}: bad outcome {t.outcome}")
        unknown = [n for n in t.tools if n not in TOOLS_BY_NAME]
        if unknown or not t.tools:
            problems.append(f"{where}: unknown or missing tools {unknown}")
            continue
        if not t.dev_text:
            problems.append(f"{where}: missing dev_text")
        if not t.args:
            continue

        # The labelled args must belong to at least one of the labelled tools
        fitting = [n for n in t.tools if set(t.args) <= set(TOOLS_BY_NAME[n].params)]
        if not fitting:
            problems.append(f"{where}: args {sorted(t.args)} don't fit any of {t.tools}")
            continue

        # If the args (plus the customer ID) are a complete call, run it for real
        tool = TOOLS_BY_NAME[fitting[0]]
        args = dict(t.args)
        if "customer_id" in tool.params and "customer_id" not in args:
            args["customer_id"] = s.customer
        required = {n for n, p in tool.params.items() if p.required}
        if required <= set(args) and tool.risk == "read":
            try:
                HANDLERS[tool.name](**args)
                ran += 1
            except ToolError as e:
                problems.append(f"{where}: {tool.name}({args}) failed: {e.code}: {e.message}")

turns = [t for s in SCENARIOS for t in s.turns]
if problems:
    print(f"{len(problems)} problem(s):")
    for p in problems:
        print("  -", p)
    raise SystemExit(1)

print(f"OK: {len(SCENARIOS)} scenarios, {len(turns)} turns "
      f"({sum(not s.multi_turn for s in SCENARIOS)} single-turn, {sum(s.multi_turn for s in SCENARIOS)} conversations)")
print(f"Ran {ran} labelled read-only calls against the backend: all worked")
print("Outcomes:", dict(Counter(t.outcome for t in turns)))
print("Tags:", dict(Counter(tag for s in SCENARIOS for tag in s.tags + [x for t in s.turns for x in t.tags])))
print(f"Distinct correct tools: {len({n for t in turns for n in t.tools})} of {len(TOOLS_BY_NAME)}")
print("By customer:", dict(Counter(s.customer for s in SCENARIOS)))
