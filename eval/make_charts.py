"""Draw every chart used in the README from the saved results (no API calls).

Run from the project folder:  python -m eval.make_charts
Writes PNGs to docs/charts/.
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402

ROOT = Path(__file__).parent.parent
OUT = ROOT / "docs" / "charts"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0"
OURS, OTHER, ACCENT2, ACCENT3 = "#2a78d6", "#b9b8b2", "#eb6834", "#1baf7a"


def load(path):
    return json.loads((ROOT / path).read_text())


def style(ax, title=None, xlabel=None, ylabel=None, grid_axis="x"):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, left=False)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, color=INK, fontsize=11.5, loc="left", pad=10)
    if xlabel:
        ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK2, fontsize=9)


def figure(w, h, n=1):
    fig, axes = plt.subplots(1, n, figsize=(w, h), dpi=160)
    fig.set_facecolor(SURFACE)
    return fig, axes


def note(fig, text):
    fig.text(0.01, 0.01, text, fontsize=7.5, color=INK2, ha="left", va="bottom")


def save(fig, name):
    fig.savefig(OUT / name, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print("  wrote", f"docs/charts/{name}")


def hbars(ax, labels, values, highlight, fmt, xmax=None):
    colors = [OURS if l == highlight else OTHER for l in labels]
    y = range(len(labels))
    ax.barh(list(y), values, color=colors, height=0.55)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, color=INK, fontsize=9)
    ax.invert_yaxis()
    top = xmax or max(values) * 1.18
    ax.set_xlim(0, top)
    for i, v in enumerate(values):
        ax.text(v + top * 0.015, i, fmt(v), va="center", fontsize=8.5,
                color=INK, fontweight="bold" if labels[i] == highlight else "normal")


# ---------------------------------------------------------------- 1. headline: end-to-end
def headline():
    s = load("results/step11/rescored.json")
    order = [("all_tools", "All 111 tools"), ("states", "Hand-built states"), ("router_k5", "Router, K=5"),
             ("router_k8", "Router, K=8")]
    labels = [l for _, l in order]
    fig, (a, b, c) = figure(12.5, 3.2, 3)
    hbars(a, labels, [s[k]["tokens"] for k, _ in order], "Router, K=8", lambda v: f"{v:,.0f}")
    style(a, "Prompt tokens per turn")
    a.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v / 1000:.0f}k"))
    hbars(b, labels, [s[k]["reasonable"] for k, _ in order], "Router, K=8", lambda v: f"{v:.1f}%", xmax=118)
    style(b, "Turns handled well")
    b.set_yticklabels([])
    b.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%" if v <= 100 else ""))
    hbars(c, labels, [s[k]["inr_1k"] for k, _ in order], "Router, K=8", lambda v: f"₹{v:,.0f}")
    style(c, "Cost per 1,000 turns (list price)")
    c.set_yticklabels([])
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    note(fig, "Full agent + safety layer + sarvam-105b-conversations, 62 identical test turns per setup "
              "(independent Hinglish/codemix set). Router K=8 vs all tools on quality: 2 vs 4 turns, a tie within noise.")
    save(fig, "headline_end_to_end.png")


# ---------------------------------------------------------------- 2. tokens grow with tools
def tokens_vs_tools():
    rows = load("results/step6/summary.json")["rows"]
    x = [r["tools"] for r in rows]
    y = [r["prompt_tokens"] for r in rows]
    fig, ax = figure(7, 3.8)
    style(ax, "Tool definitions alone eat the context window", "Tools sent to the model", "Prompt tokens", "y")
    ax.plot(x, y, color=OURS, linewidth=2, marker="o", markersize=5)
    ax.axhline(32_000, color=INK2, linewidth=1, linestyle="--")
    ax.text(x[0], 31_000, "32K context window (sarvam-105b-conversations)", fontsize=8, color=INK2, va="top")
    for xi, yi in zip(x, y):
        if xi in (5, 111):
            ax.annotate(f"{yi:,} ({100 * yi / 32000:.0f}%)", (xi, yi), textcoords="offset points", xytext=(0, 9),
                        ha="center", fontsize=8.5, color=INK)
    ax.set_xticks(x)
    ax.set_ylim(0, 34_500)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    note(fig, "~107 tokens per tool, measured over 120 API calls (24 per size).")
    save(fig, "tokens_vs_tools.png")


# ---------------------------------------------------------------- 3. technique ladder
def ladder():
    core = {r["name"]: r for r in load("results/step8b/research.json")["core"]}
    steps = {r["name"]: r for r in load("results/step9/states_vs_router.json")["rows"]}
    items = [
        ("Word matching (BM25)", core["bm25"]["llm"]["all_needed"]),
        ("Meaning matching (e5)", core["dense e5"]["llm"]["all_needed"]),
        ("Hybrid: 70% meaning + 30% words", core["hybrid weighted"]["llm"]["all_needed"]),
        ("Hybrid, rank fusion (RRF)", core["hybrid RRF"]["llm"]["all_needed"]),
        ("+ history, helpers, always-on (K=5)", steps["Router K=5"]["llm"]["all_needed"]),
        ("Final router (K=8)", steps["Router K=8"]["llm"]["all_needed"]),
    ]
    fig, ax = figure(7.5, 3.4)
    hbars(ax, [i[0] for i in items], [i[1] for i in items], "Final router (K=8)", lambda v: f"{v:.1f}%", xmax=112)
    style(ax, "Turns where every needed tool was picked")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%" if v <= 100 else ""))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    note(fig, "Independent test set: 210 turns written by sarvam-105b playing the caller. First four rows keep top 5, no history.")
    save(fig, "technique_ladder.png")


# ---------------------------------------------------------------- 4. choosing K
def k_sweep():
    sweep = load("results/step8/ablation.json")["sweep"]
    names = {"Keywords (BM25)": ("Word matching", OTHER), "Meaning (dense e5)": ("Meaning matching", ACCENT2),
             "Hybrid + history + expansion": ("Full router", OURS)}
    fig, ax = figure(7, 3.8)
    style(ax, "How many tools to keep (K)", "K = top-scoring tools kept (helpers added after)",
          "Every needed tool picked", "y")
    for key, pts in sweep.items():
        label, color = names[key]
        xs, ys = [p["k"] for p in pts], [p["all_needed"] for p in pts]
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=4.5, label=label)
        ax.annotate(f"{ys[-1]:.1f}%", (xs[-1], ys[-1]), textcoords="offset points", xytext=(7, 0), va="center",
                    fontsize=8, color=INK)
    ax.axvline(8, color=GRID, linewidth=6, zorder=0)
    ax.text(8.3, 52, "chosen: K=8", fontsize=8, color=INK2)
    ax.set_xticks([p["k"] for p in next(iter(sweep.values()))])
    ax.set_ylim(50, 102)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.legend(frameon=False, fontsize=8, labelcolor=INK, loc="lower right")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    note(fig, "Dev set: 213 typed turns (3 styles). Recall levels off after K=5-8; each extra tool costs ~107 tokens.")
    save(fig, "k_sweep.png")


# ---------------------------------------------------------------- 5. scaling to big catalogs
def scaling():
    rows = {r["name"]: r for r in load("results/step8b/research.json")["scaling"]}
    series = [("bm25", "Word matching", OTHER), ("dense e5", "Meaning matching", ACCENT2),
              ("Step 8 best", "Router, K=5", ACCENT3), ("best now", "Router, K=8 (+ generated queries)", OURS)]
    sizes = [111, 311, 511]
    fig, ax = figure(7, 3.8)
    style(ax, "Adding 400 look-alike banking tools", "Tools in the catalog", "Every needed tool picked", "y")
    for key, label, color in series:
        ys = [rows[f"{key} @ {n} tools"]["llm"]["all_needed"] for n in sizes]
        ax.plot(sizes, ys, color=color, linewidth=2, marker="o", markersize=5, label=label)
        ax.annotate(f"{ys[-1]:.1f}%", (sizes[-1], ys[-1]), textcoords="offset points", xytext=(7, 0), va="center",
                    fontsize=8, color=INK)
    ax.set_xticks(sizes)
    ax.set_xlim(90, 560)
    ax.set_ylim(75, 101)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.legend(frameon=False, fontsize=8, labelcolor=INK, loc="lower left")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    note(fig, "Independent test set. Distractors: 400 tools from 40 neighbouring products (gold loans, insurance, UPI...). "
              "The K=8 line also uses generated example queries (plain K=8 at 111 tools: 98.1%).")
    save(fig, "scaling.png")


# ---------------------------------------------------------------- 6. states vs router
def states():
    rows = {r["name"]: r for r in load("results/step9/states_vs_router.json")["rows"]}
    order = [("States fine, keyword rules", "11 states, keyword rules"),
             ("States coarse, keyword rules", "6 states, keyword rules"),
             ("States fine, perfect switching", "11 states, perfect switching"),
             ("States coarse, perfect switching", "6 states, perfect switching"),
             ("Router K=8", "Router, K=8")]
    labels = [l for _, l in order]
    fig, (a, b) = figure(11, 3.3, 2)
    hbars(a, labels, [rows[k]["llm"]["all_needed"] for k, _ in order], "Router, K=8", lambda v: f"{v:.1f}%", xmax=118)
    style(a, "Every needed tool available")
    a.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%" if v <= 100 else ""))
    hbars(b, labels, [rows[k]["llm"]["tokens"] for k, _ in order], "Router, K=8", lambda v: f"{v:,.0f}")
    style(b, "Tool tokens per AI call")
    b.set_yticklabels([])
    b.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    note(fig, "Independent test set. 'Perfect switching' always lands in the right state: the best hand-built states "
              "could ever do. Tokens include one 'go to state' function per other state.")
    save(fig, "states_vs_router.png")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams["font.family"] = "DejaVu Sans"
    for fn in (headline, tokens_vs_tools, ladder, k_sweep, scaling, states):
        fn()


if __name__ == "__main__":
    main()
