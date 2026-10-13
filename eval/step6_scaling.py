"""Step 6: how do prompt tokens and latency grow with the number of tools?

For each tool count (5, 10, 25, 50, 111) we send the same 8 questions, 3 times each,
and record prompt tokens, latency and which tool the model picked. Only the FIRST
model call of a turn is measured (the one that chooses the tool).

The correct tool is always included, plus random other tools to reach the count.

Run from the project folder:
    python -m eval.step6_scaling --dry-run    # show what it will cost, spend nothing
    python -m eval.step6_scaling              # run (asks before spending)
    python -m eval.step6_scaling --report     # rebuild summary + charts from saved results, no API calls

Saved results are reused, so running it again only pays for what's missing.
"""
import argparse
import json
import random
import statistics
from pathlib import Path

from tools import TOOLS, schemas

SIZES = [5, 10, 25, 50, 111]
REPEATS = 3
PRICE_INPUT, PRICE_CACHED, PRICE_OUTPUT = 15, 5, 60  # ₹ per 1M tokens (sarvam-105b-conversations)
CONTEXT_WINDOW = 32_000

QUESTIONS = [
    ("Loan L1001 ki agli EMI kab hai?", "get_next_emi"),
    ("Mera card CC2001 kho gaya hai.", "block_card"),
    ("Home loan L1002 ka kitna paisa release hua hai?", "get_home_loan_disbursement_status"),
    ("मेरे लोन L1007 पर कुल कितना बकाया है?", "get_outstanding_amount"),
    ("What's the bill on my card CC2001?", "get_card_bill"),
    ("FD3001 kab mature hogi?", "get_deposit_details"),
    ("Car loan L1005 band karne mein kitna lagega?", "get_vehicle_loan_foreclosure_quote"),
    ("Nearest branch for pincode 560001?", "find_nearest_branch"),
]

SYSTEM = ("You are a phone support agent for Demo Lending Co. The caller is customer C001. "
          "Use tools to answer. Keep replies short.")

OUT_DIR = Path(__file__).parent.parent / "results" / "step6"
RAW = OUT_DIR / "raw.jsonl"
ALL_NAMES = [t.name for t in TOOLS]


def tool_subset(correct, size, repeat):
    """The correct tool + random others. Same seed -> same subset on every rerun."""
    if size >= len(ALL_NAMES):
        return ALL_NAMES
    rng = random.Random(f"{correct}-{size}-{repeat}")
    others = rng.sample([n for n in ALL_NAMES if n != correct], size - 1)
    names = others + [correct]
    rng.shuffle(names)
    return names


def planned_runs():
    runs = [(size, qi, rep) for size in SIZES for qi in range(len(QUESTIONS)) for rep in range(REPEATS)]
    random.Random(0).shuffle(runs)  # interleave sizes so slow moments of the API don't bias one size
    return runs


def load_done():
    if not RAW.exists():
        return {}
    done = {}
    for line in RAW.read_text().splitlines():
        r = json.loads(line)
        done[(r["size"], r["question"], r["repeat"])] = r
    return done


def estimate_cost(runs):
    tokens = sum(300 + 108 * size for size, _, _ in runs)  # ~108 tokens per tool, measured in Step 4
    return tokens, tokens * PRICE_INPUT / 1e6


def run(runs):
    from agent.sarvam_client import chat  # imported here so --report works without an API key

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(RAW, "a") as f:
        for i, (size, qi, rep) in enumerate(runs, 1):
            question, correct = QUESTIONS[qi]
            names = tool_subset(correct, size, rep)
            try:
                r = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}],
                         tools=schemas(names))
            except Exception as e:
                print(f"  [{i}/{len(runs)}] size={size} q={qi} failed: {e}")
                continue
            picked = [tc["function"]["name"] for tc in r["tool_calls"]]
            record = {"size": size, "question": qi, "repeat": rep, "correct_tool": correct, "picked": picked,
                      "is_correct": picked[:1] == [correct], "prompt_tokens": r["usage"].get("prompt_tokens"),
                      "completion_tokens": r["usage"].get("completion_tokens"), "latency_ms": r["latency_ms"],
                      "usage": r["usage"]}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  [{i}/{len(runs)}] {size:>3} tools  {record['prompt_tokens']:>6} tokens  "
                  f"{record['latency_ms']:>5} ms  {'ok' if record['is_correct'] else 'WRONG ' + str(picked)}")


def percentile(values, p):
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def report():
    records = list(load_done().values())
    if not records:
        raise SystemExit("No results yet. Run without --report first.")

    rows = []
    for size in SIZES:
        rs = [r for r in records if r["size"] == size]
        if not rs:
            continue
        tokens = statistics.mean(r["prompt_tokens"] for r in rs)
        lat = [r["latency_ms"] for r in rs]
        rows.append({
            "tools": size, "runs": len(rs),
            "prompt_tokens": round(tokens),
            "context_used_pct": round(100 * tokens / CONTEXT_WINDOW, 1),
            "latency_p50_ms": round(percentile(lat, 50)), "latency_p95_ms": round(percentile(lat, 95)),
            "correct": sum(r["is_correct"] for r in rs), "accuracy_pct": round(100 * sum(r["is_correct"] for r in rs) / len(rs)),
            # A typical call: 8 turns x 2 model calls, tool definitions sent every time
            "cost_per_1000_calls_inr": round(tokens * 16 * 1000 * PRICE_INPUT / 1e6),
            "cost_per_1000_calls_if_cached_inr": round(tokens * 16 * 1000 * PRICE_CACHED / 1e6),
        })

    # Tokens per tool: straight-line fit of prompt tokens against tool count
    xs = [r["size"] for r in records]
    ys = [r["prompt_tokens"] for r in records]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    cached_seen = any((r["usage"].get("prompt_tokens_details") or {}).get("cached_tokens") for r in records)
    summary = {"tokens_per_tool": round(slope, 1), "base_tokens": round(my - slope * mx),
               "cached_tokens_reported": cached_seen, "rows": rows,
               "wrong_picks": [{"size": r["size"], "question": QUESTIONS[r["question"]][0],
                                "expected": r["correct_tool"], "picked": r["picked"]}
                               for r in records if not r["is_correct"]]}
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"\nTokens per tool: ~{summary['tokens_per_tool']}  (base prompt ~{summary['base_tokens']})")
    print(f"Cached tokens reported by the API: {'yes' if cached_seen else 'no'}\n")
    print(f"{'tools':>5} {'runs':>4} {'tokens':>7} {'window':>7} {'p50 ms':>7} {'p95 ms':>7} {'correct':>8} "
          f"{'₹/1k calls':>10} {'(cached)':>9}")
    for r in rows:
        print(f"{r['tools']:>5} {r['runs']:>4} {r['prompt_tokens']:>7,} {r['context_used_pct']:>6}% "
              f"{r['latency_p50_ms']:>7} {r['latency_p95_ms']:>7} {r['correct']:>4}/{r['runs']:<3} "
              f"{r['cost_per_1000_calls_inr']:>10,} {r['cost_per_1000_calls_if_cached_inr']:>9,}")
    for w in summary["wrong_picks"]:
        print(f"  wrong pick at {w['size']} tools: '{w['question']}' expected {w['expected']}, got {w['picked']}")
    charts(rows)
    print(f"\nSaved {OUT_DIR.relative_to(Path.cwd())}/summary.json, tokens.png, latency.png")


def charts(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker

    surface, ink, ink2, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0"
    blue, orange = "#2a78d6", "#eb6834"
    x = [r["tools"] for r in rows]

    def frame(ax, title, ylabel):
        ax.set_facecolor(surface)
        ax.figure.set_facecolor(surface)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(grid)
        ax.tick_params(colors=ink2, labelsize=9)
        ax.grid(axis="y", color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_title(title, color=ink, fontsize=12, loc="left", pad=12)
        ax.set_xlabel("Tools sent to the model", color=ink2, fontsize=9)
        ax.set_ylabel(ylabel, color=ink2, fontsize=9)
        ax.set_xticks(x)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))

    # Chart 1: prompt tokens, with the 32K context window for reference
    fig, ax = plt.subplots(figsize=(7, 4), dpi=150)
    frame(ax, "Prompt tokens per model call", "Prompt tokens")
    y = [r["prompt_tokens"] for r in rows]
    ax.plot(x, y, color=blue, linewidth=2, marker="o", markersize=6)
    ax.axhline(CONTEXT_WINDOW, color=ink2, linewidth=1, linestyle="--")
    ax.text(x[0], CONTEXT_WINDOW * 0.96, "32K context window (sarvam-105b-conversations)", color=ink2,
            fontsize=8, va="top")
    for xi, yi, r in zip(x, y, rows):
        if xi in (x[0], x[-1]):
            ax.annotate(f"{yi:,} ({r['context_used_pct']}%)", (xi, yi), textcoords="offset points",
                        xytext=(0, 9), ha="center", fontsize=8, color=ink)
    ax.set_ylim(0, CONTEXT_WINDOW * 1.08)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "tokens.png")
    plt.close(fig)

    # Chart 2: latency p50 and p95
    fig, ax = plt.subplots(figsize=(7, 4), dpi=150)
    frame(ax, "Model latency per call (tool choice)", "Milliseconds")
    p50 = [r["latency_p50_ms"] for r in rows]
    p95 = [r["latency_p95_ms"] for r in rows]
    ax.plot(x, p95, color=orange, linewidth=2, marker="o", markersize=6, label="p95")
    ax.plot(x, p50, color=blue, linewidth=2, marker="o", markersize=6, label="p50 (median)")
    ax.annotate("p95", (x[-1], p95[-1]), textcoords="offset points", xytext=(8, 0), va="center", fontsize=8, color=ink)
    ax.annotate("p50", (x[-1], p50[-1]), textcoords="offset points", xytext=(8, 0), va="center", fontsize=8, color=ink)
    ax.legend(frameon=False, fontsize=8, labelcolor=ink, loc="upper left")
    ax.set_ylim(0, max(p95) * 1.2)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "latency.png")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--yes", action="store_true", help="don't ask before spending")
    args = parser.parse_args()

    if args.report:
        return report()

    done = load_done()
    todo = [r for r in planned_runs() if r not in done]
    tokens, cost = estimate_cost(todo)
    print(f"{len(done)} runs already saved, {len(todo)} to do: ~{tokens:,} prompt tokens, about ₹{cost:.1f}")
    if args.dry_run or not todo:
        return report() if not todo and done else None
    if not args.yes and input("Go ahead? [y/N] ").strip().lower() != "y":
        return
    run(todo)
    report()


if __name__ == "__main__":
    main()
