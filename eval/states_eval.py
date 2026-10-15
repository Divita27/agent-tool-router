"""Step 9: hand-designed states vs the automatic router, on the same test sentences.

Run from the project folder:  python -m eval.states_eval
Free: no LLM calls. Results in results/step9/.
"""
import json
import statistics
from pathlib import Path

from eval.research_eval import DATASETS, run
from eval.router_eval import TAGS, load
from router.router import ALWAYS_ON, RouterConfig
from router.states import DESIGNS, GLOBAL, RULES, StateRouter

OUT = Path(__file__).parent.parent / "results" / "step9"
FULL = dict(method="hybrid", history=True, short_boost=True, expand=True, always_on=ALWAYS_ON)


def run_states(design, mode, rows):
    sr = StateRouter(design, mode)
    res = []
    for conv in sorted({r["conversation"] for r in rows}):
        sr.reset()
        for r in sorted((x for x in rows if x["conversation"] == conv), key=lambda x: x["turn"]):
            tools, switched = sr.select(r["text"], r["all_of"])
            s = set(tools)
            res.append({"tags": r["tags"], "turn": r["turn"], "style": r.get("language"),
                        "all_needed": all(s & set(g) for g in r["all_of"]),
                        "tools": len(tools), "tokens": sr.tokens(tools), "switched": switched,
                        "state": sr.current, "text": r["text"], "expected": r["all_of"]})
    return res


def pct(xs, key="all_needed"):
    return round(100 * sum(x[key] for x in xs) / len(xs), 1) if xs else None


def summarize(res):
    later = [x for x in res if x["turn"] > 1]
    return {"all_needed": pct(res), "tools": round(statistics.mean(x["tools"] for x in res), 1),
            "tokens": round(statistics.mean(x["tokens"] for x in res)),
            "switch_rate_later_turns": pct(later, "switched"),
            "by_tag": {t: pct([x for x in res if t in x["tags"]]) for t in TAGS},
            "later_turns": pct(later)}


def main():
    data = {k: load(v, None) for k, v in DATASETS.items()}
    OUT.mkdir(parents=True, exist_ok=True)
    rows, details = [], {}
    for design in DESIGNS:
        for mode in ("rules", "oracle"):
            name = f"States {design}, {'keyword rules' if mode == 'rules' else 'perfect switching'}"
            row = {"name": name}
            for key, recs in data.items():
                res = run_states(design, mode, recs)
                row[key] = summarize(res)
                details[f"{name} | {key}"] = [x for x in res if not x["all_needed"]]
            rows.append(row)
    for label, cfg in (("Router K=5", RouterConfig(k=5, **FULL)), ("Router K=8", RouterConfig(k=8, **FULL)),
                       ("Router K=8 + generated queries", RouterConfig(k=8, expansion="multi", **FULL))):
        r = run(RouterConfig(**{**vars(cfg), "name": label}), data)
        rows.append({"name": label, **{k: {"all_needed": r[k]["all_needed"], "tools": r[k]["tools"],
                                           "tokens": r[k]["tokens"], "switch_rate_later_turns": 0.0,
                                           "by_tag": r[k]["by_tag"], "later_turns": r[k]["later_turns"]}
                                       for k in data}})

    print(f"\n{'approach':44} {'dev':>6} {'indep':>6} {'tools':>6} {'tokens':>7} {'switch%':>8}")
    for r in rows:
        d, l = r["dev"], r["llm"]
        print(f"{r['name']:44} {d['all_needed']:>5}% {l['all_needed']:>5}% {l['tools']:>6} {l['tokens']:>7,} "
              f"{l['switch_rate_later_turns']:>7}%")
    print("\nIndependent set, all-needed by kind of request (%):")
    print(f"{'approach':44} " + " ".join(f"{t[:9]:>9}" for t in TAGS) + f" {'turn 2+':>8}")
    for r in rows:
        l = r["llm"]
        print(f"{r['name']:44} " + " ".join(f"{str(l['by_tag'][t]):>9}" for t in TAGS) + f" {str(l['later_turns']):>8}")

    print("\nSetup effort (manual decisions):")
    for design, states in DESIGNS.items():
        print(f"  states {design}: {len(states)} states, {sum(map(len, states.values()))} tool-to-state "
              f"assignments, {len(GLOBAL)} globals, {len(RULES[design])} keyword rules, "
              f"{len(states) * (len(states) - 1)} possible transitions")
    from tools import TOOLS
    print(f"  router: per tool only (description + 3 examples already in the catalog), "
          f"{sum(bool(t.needs or t.next) for t in TOOLS)} tools with needs/next links, 3 always-on tools")

    for key in ("States fine, keyword rules | llm", "States coarse, perfect switching | llm"):
        print(f"\nSample misses: {key}")
        for x in details[key][:8]:
            print(f"  [{x['state']}] {x['text'][:70]!r} needs {x['expected']}")
    (OUT / "states_vs_router.json").write_text(json.dumps({"rows": rows, "misses": details}, ensure_ascii=False,
                                                         indent=1))
    print(f"\nSaved {OUT.relative_to(Path.cwd())}/states_vs_router.json")


if __name__ == "__main__":
    main()
