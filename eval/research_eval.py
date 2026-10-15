"""Research round: compare newer tool-narrowing techniques on two test sets.

    python -m eval.research_eval                 # everything except the LLM cascade (free)
    python -m eval.research_eval --cascade       # also the cascade (calls sarvam-105b when unsure; cached)

Test sets:
    dev   eval/data/dev_typed.jsonl   213 turns typed by Claude (3 styles)
    llm   eval/data/llm_test.jsonl    210 turns, sarvam-105b playing the caller from the scenario cards
Neither was used to build tool documents or to fine-tune the model.
"""
import argparse
import json
import statistics
from pathlib import Path

from eval.router_eval import evaluate, load, summarize
from router.index import MODELS
from router.router import ALWAYS_ON, Router, RouterConfig

OUT = Path(__file__).parent.parent / "results" / "step8b"
DATASETS = {"dev": "eval/data/dev_typed.jsonl", "llm": "eval/data/llm_test.jsonl"}
FT_READY = Path(MODELS["e5-ft"][0]).exists()

C = RouterConfig
# Retrieval core only: no history, no expansion, no always-on. Isolates how well each technique ranks.
CORE = [
    C("bm25", method="bm25"),
    C("bm25 + generated queries", method="bm25", expansion="append"),
    C("dense e5", method="dense"),
    C("dense e5 + generated queries (appended)", method="dense", expansion="append"),
    C("dense e5 + generated queries (multi-vector)", method="dense", expansion="multi"),
    C("dense e5 fine-tuned", method="dense", model="e5-ft"),
    C("dense e5 fine-tuned + multi-vector", method="dense", model="e5-ft", expansion="multi"),
    C("hybrid weighted", method="hybrid"),
    C("hybrid RRF", method="hybrid", fusion="rrf"),
    C("hybrid + multi-vector", method="hybrid", expansion="multi"),
    C("hybrid fine-tuned + multi-vector", method="hybrid", model="e5-ft", expansion="multi"),
]

FULL = dict(history=True, short_boost=True, expand=True, always_on=ALWAYS_ON)
PIPELINE = [
    C("P0 Step 8 best (hybrid e5)", method="hybrid", **FULL),
    C("P1 + multi-vector queries", method="hybrid", expansion="multi", **FULL),
    C("P2 + fine-tuned model", method="hybrid", model="e5-ft", **FULL),
    C("P3 + fine-tuned + multi-vector", method="hybrid", model="e5-ft", expansion="multi", **FULL),
    C("P4 P1 with RRF fusion", method="hybrid", expansion="multi", fusion="rrf", **FULL),
    C("P5 P1 + sticky 2", method="hybrid", expansion="multi", sticky=2, **FULL),
    C("P6 P1 with k=3", method="hybrid", expansion="multi", k=3, **FULL),
    C("P7 P1 with k=8", method="hybrid", expansion="multi", k=8, **FULL),
]


def usable(cfg):
    return FT_READY or cfg.model != "e5-ft"


def run(cfg, data, rewrite_fn=None):
    router = Router(cfg, rewrite_fn=rewrite_fn)
    row = {"name": cfg.name}
    for key, rows in data.items():
        router.cascade_calls = 0
        res = evaluate(router, rows)
        s = summarize(cfg.name, res)
        row[key] = {k: s[k] for k in ("hit", "all_needed", "tools", "tokens", "token_saving_pct", "ms_p50",
                                      "by_style", "by_tag", "later_turns")}
        row[key]["cascade_rate"] = round(100 * router.cascade_calls / len(rows), 1)
        row[key]["misses"] = [{"text": x["text"], "expected": x["expected"], "chosen": x["chosen"][:8]}
                              for x in res if not x["all_needed"]]
    return row


def show(title, rows):
    print(f"\n=== {title} ===")
    print(f"{'technique':46} {'dev':>6} {'llm':>6} {'tools':>6} {'tokens':>7} {'ms':>6} {'llm-call%':>9}")
    for r in rows:
        d, l = r["dev"], r["llm"]
        print(f"{r['name']:46} {d['all_needed']:>5}% {l['all_needed']:>5}% {l['tools']:>6} {l['tokens']:>7,} "
              f"{l['ms_p50']:>6} {l['cascade_rate']:>8}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cascade", action="store_true")
    args = parser.parse_args()
    data = {k: load(v, None) for k, v in DATASETS.items()}
    print(f"dev {len(data['dev'])} turns, llm {len(data['llm'])} turns. Fine-tuned model ready: {FT_READY}")
    OUT.mkdir(parents=True, exist_ok=True)
    out = {}

    out["core"] = [run(c, data) for c in CORE if usable(c)]
    show("Retrieval core, k=5 (all-needed %)", out["core"])
    out["pipeline"] = [run(c, data) for c in PIPELINE if usable(c)]
    show("Full pipeline: history + expansion + always-on (all-needed %)", out["pipeline"])

    best = max(out["pipeline"], key=lambda r: (r["llm"]["all_needed"] + r["dev"]["all_needed"], -r["llm"]["tokens"]))
    best_cfg = next(c for c in PIPELINE if c.name == best["name"])
    print(f"\nBest pipeline: {best['name']}")

    # Scaling: add 200 and 400 distractor tools from neighbouring banking products
    scale = []
    for n in (0, 200, 400):
        for label, cfg in (("bm25", C(method="bm25")), ("dense e5", C(method="dense")),
                           ("Step 8 best", PIPELINE[0]), ("best now", best_cfg)):
            c = RouterConfig(**{**vars(cfg), "name": f"{label} @ {111 + n} tools", "distractors": n})
            scale.append(run(c, data))
    out["scaling"] = scale
    show("Scaling: 111 -> 311 -> 511 tools (all-needed %)", scale)

    if args.cascade:
        from eval import rewrite
        casc = []
        for margin in (0.02, 0.05, 0.10, 1.0):  # 1.0 = always rewrite (upper bound)
            c = RouterConfig(**{**vars(best_cfg), "name": f"cascade margin {margin}", "cascade": True,
                                "cascade_margin": margin})
            casc.append(run(c, data, rewrite_fn=rewrite.rewrite))
        out["cascade"] = casc
        show("Cascade: LLM rewrite only when unsure (all-needed %, llm-call% = turns that called the LLM)", casc)
        lat = rewrite.cached_latencies()
        if lat:
            print(f"Rewrite call latency: p50 {statistics.median(lat):.0f} ms, "
                  f"p95 {sorted(lat)[int(0.95 * (len(lat) - 1))]:.0f} ms over {len(lat)} cached calls. "
                  f"This session: {rewrite.stats['calls']} new calls, "
                  f"~₹{(rewrite.stats['prompt_tokens'] * 15 + rewrite.stats['completion_tokens'] * 60) / 1e6:.2f}")

    (OUT / "research.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\nSaved {OUT.relative_to(Path.cwd())}/research.json")


if __name__ == "__main__":
    main()
