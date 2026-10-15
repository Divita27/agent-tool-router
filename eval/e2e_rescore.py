"""Re-score Step 11 results from saved runs (no API calls).

    strict      acted this turn: called (or tried; safety-held counts) a right tool, every group for multi-requests
    reasonable  strict, OR asked the caller a question while NOT calling any wrong action tool and NOT
                asking for an internal ID it could look up
    bad: asked for an internal ID  ("loan ID", "card ID", "payment ID", ...) which the prompt forbids
    bad: wrong action               called a write/payment/pii/irreversible tool that wasn't right for the turn
Also: paired comparison, i.e. on how many turns one setup succeeded where the other failed.
"""
import json
import re
import statistics
from pathlib import Path

from eval.e2e_eval import SETUPS, cost
from tools import TOOLS_BY_NAME

RAW = Path("results/step11/raw.jsonl")
ASKS_ID = re.compile(r"\b(loan|card|payment|customer|account|application|policy)\s*(id|number|नंबर)\b|\bआईडी\b|"
                     r"\bID\b", re.I)


def judge(r):
    actions = [n for n in r["called"] if n in TOOLS_BY_NAME and TOOLS_BY_NAME[n].risk != "read"
               and n not in ("transfer_to_human",)]
    right = {n for g in r["expected"] for n in g}
    wrong_action = [n for n in actions if n not in right]
    asked_id = bool(ASKS_ID.search(r["reply"])) and "?" in r["reply"] + "？"
    asked = "?" in r["reply"] or "？" in r["reply"]
    reasonable = r["ok"] or (asked and not wrong_action and not asked_id)
    if r["outcome"] == "clarify":
        reasonable = r["ok"]
    return {"strict": r["ok"], "reasonable": reasonable, "asked_id": asked_id, "wrong_action": bool(wrong_action)}


rows = [json.loads(l) for l in RAW.read_text().splitlines()]
by = {}
for r in rows:
    by.setdefault(r["setup"], {})[r["id"]] = r
common = set.intersection(*(set(v) for v in by.values()))
print(f"{len(common)} turns run by all setups\n")
print(f"{'setup':11} {'strict':>7} {'reasonable':>11} {'asked ID':>9} {'wrong act':>10} {'args ok':>8} "
      f"{'tok/turn':>9} {'calls':>6} {'p50 ms':>7} {'p95 ms':>7} {'₹/1k':>6}")
summary = {}
for s in SETUPS:
    rs = [by[s][i] for i in sorted(common)]
    j = [judge(r) for r in rs]
    a = [r["args_ok"] for r in rs if r["args_ok"] is not None]
    ms = sorted(r["ms"] for r in rs)
    summary[s] = {
        "strict": round(100 * statistics.mean(x["strict"] for x in j), 1),
        "reasonable": round(100 * statistics.mean(x["reasonable"] for x in j), 1),
        "asked_id": sum(x["asked_id"] for x in j), "wrong_action": sum(x["wrong_action"] for x in j),
        "args_ok": round(100 * sum(a) / len(a), 1) if a else None,
        "tokens": round(statistics.mean(r["prompt_tokens"] for r in rs)),
        "calls": round(statistics.mean(r["model_calls"] for r in rs), 2),
        "p50": statistics.median(ms), "p95": ms[int(0.95 * (len(ms) - 1))],
        "inr_1k": round(1000 * statistics.mean(map(cost, rs)), 1),
    }
    v = summary[s]
    print(f"{s:11} {v['strict']:>6}% {v['reasonable']:>10}% {v['asked_id']:>9} {v['wrong_action']:>10} "
          f"{v['args_ok']:>7}% {v['tokens']:>9,} {v['calls']:>6} {v['p50']:>7} {v['p95']:>7} {v['inr_1k']:>6}")

print("\nPaired: turns where A was reasonable and B wasn't (A wins / B wins)")
for a, b in (("router_k8", "all_tools"), ("router_k8", "states"), ("router_k5", "all_tools"), ("states", "all_tools")):
    aw = sum(judge(by[a][i])["reasonable"] and not judge(by[b][i])["reasonable"] for i in common)
    bw = sum(judge(by[b][i])["reasonable"] and not judge(by[a][i])["reasonable"] for i in common)
    print(f"  {a} vs {b}: {aw} / {bw}")

print("\nStill not reasonable, router_k8:")
for i in sorted(common):
    r = by["router_k8"][i]
    j = judge(r)
    if not j["reasonable"]:
        why = "asked for an ID" if j["asked_id"] else "wrong action" if j["wrong_action"] else "no question, no action"
        print(f"  {i[15:]:8} {why:22} called {r['called']}  {r['reply'][:90]!r}")
Path("results/step11/rescored.json").write_text(json.dumps(summary, indent=1))
