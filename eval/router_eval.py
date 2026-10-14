"""Compare tool-narrowing techniques offline (no LLM calls, no cost).

Run from the project folder:
    python -m eval.router_eval                         # dev set (typed by Claude)
    python -m eval.router_eval --data eval/data/test_set.jsonl --split dev   # real recordings, later

For each technique it reports, over every turn:
    hit         the main correct tool is in the selected set
    all-needed  EVERY tool the turn needs is in the set (matters for chains and multi-requests)
    tools       how many tools were sent
    tokens      prompt tokens those tool definitions cost (all 111 = ~11,900)
    ms          routing time per turn
"""
import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from router.router import ALWAYS_ON, Router, RouterConfig, tokens_for

OUT = Path(__file__).parent.parent / "results" / "step8"
ALL_TOKENS = tokens_for([n for n in __import__("tools").TOOLS_BY_NAME])
TAGS = ["lookalike", "ambiguous", "reference", "topic_switch", "chain", "multi_request"]

C = RouterConfig
LADDER = [
    C("1 random", method="random"),
    C("2 bm25 (keywords)", method="bm25"),
    C("3 dense minilm", method="dense", model="minilm"),
    C("4 dense e5", method="dense", model="e5-small"),
    C("5 dense e5, no examples in docs", method="dense", model="e5-small", doc_examples=False),
    C("6 hybrid e5+bm25 a=0.7", method="hybrid", model="e5-small", alpha=0.7),
    C("7 hybrid e5+bm25 a=0.5", method="hybrid", model="e5-small", alpha=0.5),
    C("8 area-first e5 (auto 'states')", method="area", model="e5-small"),
    C("9 hybrid + history", method="hybrid", history=True),
    C("10 + split requests", method="hybrid", history=True, split=True),
    C("11 + needs/next expansion", method="hybrid", history=True, split=True, expand=True),
    C("12 + always-on tools", method="hybrid", history=True, split=True, expand=True, always_on=ALWAYS_ON),
    C("13 + adaptive k", method="hybrid", history=True, split=True, expand=True, always_on=ALWAYS_ON,
      adaptive=True),
    C("14 12 + short follow-up boost", method="hybrid", history=True, split=True, expand=True,
      always_on=ALWAYS_ON, short_boost=True),
    C("15 14 without split", method="hybrid", history=True, expand=True, always_on=ALWAYS_ON, short_boost=True),
]

# K sweep: how recall grows with K for three techniques (always-on left out so K is the only variable)
SWEEP_KS = [1, 2, 3, 5, 8, 12, 20]
SWEEP = {
    "Keywords (BM25)": dict(method="bm25"),
    "Meaning (dense e5)": dict(method="dense", model="e5-small"),
    "Hybrid + history + expansion": dict(method="hybrid", history=True, expand=True, short_boost=True),
}


def load(path, split):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
    if split:
        rows = [r for r in rows if r["split"] == split]
    by_conv = defaultdict(dict)
    for r in rows:
        by_conv[r["conversation"]][r["turn"]] = r
    for r in rows:  # context = previous turn's caller words + the agent's scripted reply
        prev = by_conv[r["conversation"]].get(r["turn"] - 1)
        r["context"] = f"{prev['text']} {prev.get('agent_reply') or ''}".strip() if prev else ""
    return rows


def evaluate(router, rows):
    results = []
    for r in rows:
        chosen, ms = router.timed_select(r["text"], r["context"])
        s = set(chosen)
        results.append({
            "id": r["id"], "style": r.get("language"), "tags": r["tags"], "turn": r["turn"],
            "hit": bool(s & set(r["tools"])),
            "all_needed": all(s & set(g) for g in r["all_of"]),
            "tools": len(chosen), "tokens": tokens_for(chosen), "ms": ms, "chosen": chosen,
            "expected": r["all_of"], "text": r["text"],
        })
    return results


def summarize(name, res):
    def pct(xs, key):
        return round(100 * sum(x[key] for x in xs) / len(xs), 1) if xs else None
    out = {
        "name": name, "turns": len(res),
        "hit": pct(res, "hit"), "all_needed": pct(res, "all_needed"),
        "tools": round(statistics.mean(x["tools"] for x in res), 1),
        "tokens": round(statistics.mean(x["tokens"] for x in res)),
        "token_saving_pct": round(100 * (1 - statistics.mean(x["tokens"] for x in res) / ALL_TOKENS), 1),
        "ms_p50": round(statistics.median(x["ms"] for x in res), 1),
        "by_style": {s: pct([x for x in res if x["style"] == s], "all_needed")
                     for s in sorted({x["style"] for x in res})},
        "by_tag": {t: pct([x for x in res if t in x["tags"]], "all_needed") for t in TAGS},
        "later_turns": pct([x for x in res if x["turn"] > 1], "all_needed"),
    }
    return out


def print_table(rows):
    print(f"\n{'technique':38} {'hit':>6} {'all-needed':>10} {'tools':>6} {'tokens':>7} {'saved':>6} {'ms':>6}")
    for s in rows:
        print(f"{s['name']:38} {s['hit']:>5}% {s['all_needed']:>9}% {s['tools']:>6} {s['tokens']:>7,} "
              f"{s['token_saving_pct']:>5}% {s['ms_p50']:>6}")
    print(f"\nall-needed by kind of request (%):")
    print(f"{'technique':38} " + " ".join(f"{t[:9]:>9}" for t in TAGS) + f" {'turn 2+':>8}")
    for s in rows:
        print(f"{s['name']:38} " + " ".join(f"{str(s['by_tag'][t]):>9}" for t in TAGS) + f" {s['later_turns']:>8}")
    print(f"\nall-needed by writing style (%):")
    for s in rows:
        print(f"{s['name']:38} " + "  ".join(f"{k} {v}" for k, v in s["by_style"].items()))


def chart_sweep(sweep):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker

    surface, ink, ink2, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0"
    colors = ["#2a78d6", "#eb6834", "#1baf7a"]  # categorical slots 1-3, validated all-pairs
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=150)
    fig.set_facecolor(surface)
    ax.set_facecolor(surface)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(grid)
    ax.tick_params(colors=ink2, labelsize=9)
    ax.grid(axis="y", color=grid, linewidth=0.8)
    ax.set_axisbelow(True)
    for (label, pts), color in zip(sweep.items(), colors):
        xs, ys = [p["k"] for p in pts], [p["all_needed"] for p in pts]
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=5, label=label)
        ax.annotate(f"{ys[-1]:.1f}%", (xs[-1], ys[-1]), textcoords="offset points", xytext=(8, 0),
                    va="center", fontsize=8, color=ink)
    ax.set_xticks(SWEEP_KS)
    ax.set_ylim(50, 101)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_title("Turns where every needed tool was selected", color=ink, fontsize=12, loc="left", pad=12)
    ax.set_xlabel("K (top-scoring tools kept, before expansion)", color=ink2, fontsize=9)
    ax.set_ylabel("All-needed recall", color=ink2, fontsize=9)
    ax.legend(frameon=False, fontsize=8, labelcolor=ink, loc="lower right")
    ax.text(0, -0.2, "Dev set: 213 typed turns (Claude-written, 3 styles). Not real speech.", transform=ax.transAxes,
            fontsize=7.5, color=ink2)
    fig.tight_layout()
    fig.savefig(OUT / "k_sweep.png")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="eval/data/dev_typed.jsonl")
    parser.add_argument("--split", default=None)
    args = parser.parse_args()
    rows = load(args.data, args.split)
    print(f"{len(rows)} turns from {args.data}. All 111 tools = {ALL_TOKENS:,} tokens.")

    OUT.mkdir(parents=True, exist_ok=True)
    summaries, details = [], {}
    for cfg in LADDER:
        res = evaluate(Router(cfg), rows)
        summaries.append(summarize(cfg.name, res))
        details[cfg.name] = res
    print_table(summaries)

    sweep = {}
    for label, kw in SWEEP.items():
        sweep[label] = []
        for k in SWEEP_KS:
            s = summarize(f"{label} k={k}", evaluate(Router(RouterConfig(k=k, **kw)), rows))
            sweep[label].append({"k": k, "all_needed": s["all_needed"], "tokens": s["tokens"], "tools": s["tools"]})
    print("\nK sweep (all-needed % / avg tools / tokens):")
    for label, pts in sweep.items():
        print(f"  {label:30} " + "  ".join(f"k{p['k']}: {p['all_needed']}% ({p['tools']}t, {p['tokens']})" for p in pts))
    chart_sweep(sweep)

    # What the best router still misses (highest all-needed, then fewest tokens)
    best = max(summaries, key=lambda s: (s["all_needed"], -s["tokens"]))["name"]
    misses = [x for x in details[best] if not x["all_needed"]]
    print(f"\nMisses for '{best}': {len(misses)}")
    for x in misses[:25]:
        missing = [g for g in x["expected"] if not set(g) & set(x["chosen"])]
        print(f"  [{x['style']}] {x['text'][:70]!r}\n      missing {missing}  got {x['chosen'][:6]}")

    (OUT / "ablation.json").write_text(json.dumps({"summaries": summaries, "sweep": sweep, "details": details},
                                                  ensure_ascii=False, indent=1))
    print(f"\nSaved {OUT.relative_to(Path.cwd())}/ablation.json")


if __name__ == "__main__":
    main()
