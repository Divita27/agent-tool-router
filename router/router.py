"""Picks the few tools the model should see on each turn.

Every technique is a switch on RouterConfig, so they can be compared one by one:

    method        how tools are scored
                    random   baseline floor: random tools
                    bm25     keyword match
                    dense    embedding similarity (meaning)
                    hybrid   dense + bm25, weighted
                    area     pick the 2 best areas first (like automatic "states"), then tools inside them
    k             how many top-scoring tools to keep
    adaptive      keep fewer or more than k, depending on where the scores drop off
    history       also score against the previous turn (caller + agent reply)
    short_boost   weight the previous turn 3x when the caller's latest words are 4 or fewer
    split         split "X aur Y" into parts and route each part
    expand        add each selected tool's `needs` and `next` tools
    always_on     tools included on every turn
"""
import random
import re
import time
from dataclasses import dataclass, field

from router.index import BM25, Embedder, TOOL_TOKENS, tool_document
from tools import TOOLS, TOOLS_BY_NAME

ALWAYS_ON = ["list_loans", "list_customer_products", "transfer_to_human"]

# Words that join separate requests: "EMI kab hai AUR card ka bill kitna"
SPLITTER = re.compile(r"\s*(?:[,?;।]|\b(?:aur|and|also|plus|phir|then|uske baad)\b|\bऔर\b|\bफिर\b)\s*", re.I)


@dataclass
class RouterConfig:
    name: str = "router"
    method: str = "hybrid"
    model: str = "e5-small"
    k: int = 5
    alpha: float = 0.7            # hybrid: weight of dense vs bm25
    adaptive: bool = False
    history: bool = False
    history_weight: float = 0.5
    short_boost: bool = False     # lean on the previous turn much more when the caller says very little
    split: bool = False
    expand: bool = False
    always_on: list = field(default_factory=list)
    doc_examples: bool = True
    seed: int = 0


class Router:
    _embedders = {}  # share loaded models between routers

    def __init__(self, config: RouterConfig):
        self.c = config
        self.names = [t.name for t in TOOLS]
        docs = {t.name: tool_document(t, with_examples=config.doc_examples) for t in TOOLS}
        self.bm25 = BM25(docs)
        self.embedder = None
        if config.method in ("dense", "hybrid", "area"):
            key = (config.model, config.doc_examples)
            if key not in Router._embedders:
                Router._embedders[key] = Embedder(config.model, docs)
            self.embedder = Router._embedders[key]
        self.rng = random.Random(config.seed)

    # ---------- scoring ----------

    def _score(self, text):
        if self.c.method == "random":
            return {n: self.rng.random() for n in self.names}
        if self.c.method == "bm25":
            return self.bm25.scores(text)
        dense = self.embedder.scores(text)
        if self.c.method in ("dense", "area"):
            return dense
        sparse = self.bm25.scores(text)
        top = max(sparse.values()) or 1.0
        return {n: self.c.alpha * dense[n] + (1 - self.c.alpha) * sparse[n] / top for n in self.names}

    def _scores_with_history(self, text, context):
        scores = self._score(text)
        if self.c.history and context:
            ctx = self._score(context)
            w = self.c.history_weight
            if self.c.short_boost and len(text.split()) <= 4:   # "personal wala", "haan kar do"
                w *= 3
            scores = {n: scores[n] + w * ctx[n] for n in self.names}
        return scores

    # ---------- choosing ----------

    def _top(self, scores, k):
        ranked = sorted(scores, key=scores.get, reverse=True)
        if self.c.method == "area":
            area_best = {}
            for n in ranked:
                area_best.setdefault(TOOLS_BY_NAME[n].area, scores[n])
            top_areas = sorted(area_best, key=area_best.get, reverse=True)[:2]
            ranked = [n for n in ranked if TOOLS_BY_NAME[n].area in top_areas]
        if not self.c.adaptive:
            return ranked[:k]
        # Adaptive: keep at least 3, at most 2k, stop at the biggest drop in score
        lo, hi = 3, min(2 * k, len(ranked))
        vals = [scores[n] for n in ranked[:hi + 1]]
        gaps = [vals[i] - vals[i + 1] for i in range(lo - 1, hi)]
        cut = lo + max(range(len(gaps)), key=gaps.__getitem__) if gaps else k
        return ranked[:cut]

    def select(self, text, context=""):
        """text: the caller's latest words. context: the previous turn (caller + agent), if any.
        Returns tool names, most relevant first."""
        parts = [p for p in SPLITTER.split(text) if len(p.split()) >= 2] if self.c.split else []
        if len(parts) > 1:
            # Route each part on its own, take the best few from each, then fill up from the whole sentence
            per_part = max(2, self.c.k // len(parts) + 1)
            chosen = []
            for p in parts:
                for n in self._top(self._scores_with_history(p, context), per_part):
                    if n not in chosen:
                        chosen.append(n)
            for n in self._top(self._scores_with_history(text, context), self.c.k):
                if len(chosen) >= self.c.k:
                    break
                if n not in chosen:
                    chosen.append(n)
        else:
            chosen = self._top(self._scores_with_history(text, context), self.c.k)

        if self.c.expand:
            for n in list(chosen):
                for linked in TOOLS_BY_NAME[n].needs + TOOLS_BY_NAME[n].next:
                    if linked not in chosen:
                        chosen.append(linked)
        for n in self.c.always_on:
            if n not in chosen:
                chosen.append(n)
        return chosen

    def timed_select(self, text, context=""):
        start = time.perf_counter()
        chosen = self.select(text, context)
        return chosen, (time.perf_counter() - start) * 1000

    # ---------- plugging into the agent ----------

    def for_agent(self):
        """Returns a select_tools(messages) function for agent.loop.Agent."""
        def select_tools(messages):
            users = [m["content"] for m in messages if m["role"] == "user"]
            context = ""
            if len(users) > 1:
                last_reply = next((m["content"] for m in reversed(messages[:-1])
                                   if m["role"] == "assistant" and m.get("content")), "")
                context = f"{users[-2]} {last_reply}"
            return self.select(users[-1], context)
        return select_tools


def tokens_for(names):
    return sum(TOOL_TOKENS[n] for n in names)
