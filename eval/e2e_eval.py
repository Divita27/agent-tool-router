"""Step 11: the full agent (router or states or all tools) + safety layer + sarvam-105b, end to end.

    python -m eval.e2e_eval --dry-run      # cost estimate only
    python -m eval.e2e_eval                # run (stops at the spending cap; resumable)
    python -m eval.e2e_eval --report       # rebuild the summary from saved results, no API calls

Test turns: the independent sarvam-105b test set, codemix style (Devanagari + English, like Saaras output),
one per scenario turn. Conversations replay the scripted history (earlier turns + scripted agent replies).

Scoring per turn (from the scenario labels):
    call      the agent called (or tried to call: held by the safety layer counts) one of the right tools,
              and for multi-requests, one tool from EVERY group
    clarify   the agent asked a question and did not attempt any of the action tools
    either    either of the above
    args      for turns with labelled arguments: the called tool's arguments match (loan, amount, date...)
"""
import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from agent.loop import Agent
from backend import seed
from eval.router_eval import TAGS, load
from router.router import ALWAYS_ON, Router, RouterConfig
from router.states import RULES, LiveStates, StateRouter

OUT = Path(__file__).parent.parent / "results" / "step11"
RAW = OUT / "raw.jsonl"
CAP_INR = 50.0
PRICE_IN, PRICE_OUT = 15, 60
EST_TOKENS = {"all_tools": 27_000, "states": 10_000, "router_k5": 3_800, "router_k8": 4_600}  # per turn, rough
SETUPS = list(EST_TOKENS)
LOOKUPS = {"list_loans", "list_customer_products", "get_customer_profile", "get_loan_details"}


def make_agent(setup, row, routers):
    if setup == "all_tools":
        return Agent(row["customer"], log=False, audit=False)
    if setup == "states":
        # Start where the conversation would be: keyword rules on earlier turns, else the main loan state
        start = None
        if row["turn"] > 1:
            start = StateRouter("coarse").detect(row["context"]) or None
        return Agent(row["customer"], log=False, audit=False, states=LiveStates("coarse", start))
    return Agent(row["customer"], select_tools=routers[setup].for_agent(), log=False, audit=False)


def replay_history(agent, row, by_conv):
    """Scripted earlier turns: caller words + the agent reply printed on the recorder's card."""
    for t in range(1, row["turn"]):
        prev = by_conv[row["conversation"]][t]
        agent.messages.append({"role": "user", "content": prev["text"]})
        agent.messages.append({"role": "assistant", "content": prev["agent_reply"]})


def args_match(expected, called_args):
    try:
        got = json.loads(called_args) if isinstance(called_args, str) else called_args
    except json.JSONDecodeError:
        return False
    for k, v in expected.items():
        if k not in got:
            return False
        a, b = got[k], v
        if isinstance(b, (int, float)):
            try:
                if abs(float(a) - float(b)) > 0.5:
                    return False
            except (TypeError, ValueError):
                return False
        elif str(a).strip().lower() != str(b).strip().lower():
            return False
    return True


def score(row, turn):
    calls = [c for m in turn.model_calls for c in m.tool_calls if not c["name"].startswith("go_to_")]
    names = [c["name"] for c in calls]
    attempted_groups = all(set(names) & set(g) for g in row["all_of"])
    attempted_any = bool(set(names) & set(row["tools"]))
    asked = "?" in turn.reply or "？" in turn.reply
    if row["outcome"] == "call":
        ok = attempted_groups
    elif row["outcome"] == "clarify":
        ok = asked and not attempted_any
    else:
        ok = attempted_groups or asked
    args_ok = None
    if row["args"] and attempted_any:
        main = [c for c in calls if c["name"] in row["tools"]]
        fitting = {k: v for k, v in row["args"].items()}
        args_ok = any(args_match(fitting, c["arguments"]) for c in main)
    return ok, args_ok, names


def run_one(setup, row, by_conv, routers):
    agent = make_agent(setup, row, routers)
    replay_history(agent, row, by_conv)
    turn = agent.respond(row["text"])
    ok, args_ok, names = score(row, turn)
    return {
        "setup": setup, "id": row["id"], "scenario": row["scenario"], "turn": row["turn"], "tags": row["tags"],
        "outcome": row["outcome"], "ok": ok, "args_ok": args_ok, "called": names,
        "expected": row["all_of"], "text": row["text"], "reply": turn.reply,
        "prompt_tokens": turn.prompt_tokens, "completion_tokens": turn.completion_tokens,
        "model_calls": len(turn.model_calls), "tools_offered": len(turn.tools_offered), "ms": turn.total_ms,
        "transitions": agent.states.transitions if agent.states else 0, "error": turn.error,
        "held_by_safety": [e["tool"] for e in agent.safety.events if e["event"] == "confirmation_requested"],
    }


def load_done():
    if not RAW.exists():
        return {}
    return {(r["setup"], r["id"]): r for r in map(json.loads, RAW.read_text().splitlines())}


def cost(r):
    return (r["prompt_tokens"] * PRICE_IN + r["completion_tokens"] * PRICE_OUT) / 1e6


def report():
    done = list(load_done().values())
    ids_by_setup = defaultdict(set)
    for r in done:
        ids_by_setup[r["setup"]].add(r["id"])
    common = set.intersection(*ids_by_setup.values()) if len(ids_by_setup) == len(SETUPS) else set()
    rows = [r for r in done if r["id"] in common]
    print(f"\n{len(common)} turns completed by all {len(SETUPS)} setups. Spent so far ≈ ₹{sum(map(cost, done)):.2f}")
    summary = {}
    print(f"\n{'setup':12} {'correct':>8} {'args ok':>8} {'prompt tok/turn':>16} {'model calls':>12} "
          f"{'ms p50':>7} {'ms p95':>7} {'₹/1k turns':>11} {'errors':>7}")
    for setup in SETUPS:
        rs = [r for r in rows if r["setup"] == setup]
        if not rs:
            continue
        a = [r["args_ok"] for r in rs if r["args_ok"] is not None]
        ms = sorted(r["ms"] for r in rs)
        s = {
            "turns": len(rs), "correct_pct": round(100 * sum(r["ok"] for r in rs) / len(rs), 1),
            "args_ok_pct": round(100 * sum(a) / len(a), 1) if a else None,
            "prompt_tokens_per_turn": round(statistics.mean(r["prompt_tokens"] for r in rs)),
            "model_calls_per_turn": round(statistics.mean(r["model_calls"] for r in rs), 2),
            "ms_p50": round(statistics.median(ms)), "ms_p95": ms[int(0.95 * (len(ms) - 1))],
            "inr_per_1000_turns": round(1000 * statistics.mean(map(cost, rs)), 1),
            "errors": sum(bool(r["error"]) for r in rs),
            "transitions_per_turn": round(statistics.mean(r["transitions"] for r in rs), 2),
            "by_outcome": {o: round(100 * statistics.mean(r["ok"] for r in rs if r["outcome"] == o), 1)
                           for o in ("call", "clarify", "either") if any(r["outcome"] == o for r in rs)},
            "by_tag": {t: round(100 * statistics.mean(r["ok"] for r in rs if t in r["tags"]), 1)
                       for t in TAGS if any(t in r["tags"] for r in rs)},
        }
        summary[setup] = s
        print(f"{setup:12} {s['correct_pct']:>7}% {str(s['args_ok_pct']) + '%':>8} {s['prompt_tokens_per_turn']:>16,} "
              f"{s['model_calls_per_turn']:>12} {s['ms_p50']:>7} {s['ms_p95']:>7} {s['inr_per_1000_turns']:>11} "
              f"{s['errors']:>7}")
    print("\nCorrect % by kind of turn:")
    kinds = ["call", "clarify", "either"] + TAGS
    print(f"{'setup':12} " + " ".join(f"{k[:9]:>9}" for k in kinds))
    for setup, s in summary.items():
        vals = [s["by_outcome"].get(k, s["by_tag"].get(k)) for k in kinds]
        print(f"{setup:12} " + " ".join(f"{str(v):>9}" for v in vals))
    if "states" in summary:
        print(f"\nStates: {summary['states']['transitions_per_turn']} LLM-driven state switches per turn")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    wrong = defaultdict(list)
    for r in rows:
        if not r["ok"]:
            wrong[r["setup"]].append({k: r[k] for k in ("id", "text", "expected", "called", "reply")})
    (OUT / "failures.json").write_text(json.dumps(wrong, ensure_ascii=False, indent=1))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--style", default="codemix")
    parser.add_argument("--limit", type=int, default=None, help="only the first N turns (pilot)")
    args = parser.parse_args()
    if args.report:
        return report()

    rows = [r for r in load("eval/data/llm_test.jsonl", None) if r["language"] == args.style]
    by_conv = defaultdict(dict)
    for r in rows:
        by_conv[r["conversation"]][r["turn"]] = r
    rows = [r for r in rows if all(t in by_conv[r["conversation"]] for t in range(1, r["turn"]))]
    if args.limit:
        rows = rows[:args.limit]
    done = load_done()
    todo = [(s, r) for r in rows for s in SETUPS if (s, r["id"]) not in done]
    est = sum(EST_TOKENS[s] for s, _ in todo) * PRICE_IN / 1e6 * 1.1
    spent = sum(map(cost, done.values()))
    print(f"{len(rows)} turns x {len(SETUPS)} setups. {len(done)} done (≈ ₹{spent:.2f}), {len(todo)} to run, "
          f"estimated ≈ ₹{est:.0f}. Cap ₹{CAP_INR:.0f}.")
    if args.dry_run or not todo:
        return report() if not todo else None

    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        seed.main()
    full = dict(method="hybrid", history=True, short_boost=True, expand=True, always_on=ALWAYS_ON)
    routers = {"router_k5": Router(RouterConfig(k=5, **full)), "router_k8": Router(RouterConfig(k=8, **full))}
    OUT.mkdir(parents=True, exist_ok=True)
    with open(RAW, "a") as f:
        for i, (setup, row) in enumerate(todo, 1):
            if spent >= CAP_INR:
                print(f"Spending cap ₹{CAP_INR:.0f} reached. Stopping (rerun later to continue).")
                break
            rec = run_one(setup, row, by_conv, routers)
            spent += cost(rec)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(f"[{i}/{len(todo)}] {setup:10} {row['id']:28} {'OK ' if rec['ok'] else 'BAD'} "
                  f"{rec['prompt_tokens']:>6} tok  called {rec['called'][:3]}  ₹{spent:.2f}", flush=True)
    report()


if __name__ == "__main__":
    main()
