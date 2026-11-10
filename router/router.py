"""Picks the few tools the model should see on each turn.

Every technique is a switch on RouterConfig, so they can be compared one by one:

    method        how tools are scored
                    random   baseline floor
                    bm25     keyword match
                    dense    embedding similarity (meaning)
                    hybrid   dense + bm25
                    area     pick the 2 best areas first (automatic "states"), then tools inside them
    model         embedding model: e5-small, minilm, e5-ft (fine-tuned on generated caller queries)
    fusion        hybrid only: "weighted" (alpha*dense + (1-alpha)*bm25) or "rrf" (reciprocal rank fusion)
    expansion     generated caller queries per tool (Re-Invoke style):
                    none     tool document only
                    append   queries appended to the document text
                    multi    each query is its own vector; a tool scores its best-matching vector
    k             how many top-scoring tools to keep
    adaptive      keep fewer or more than k, depending on where the scores drop off
    history       also score against the previous turn (caller + agent reply)
    short_boost   weight the previous turn 3x when the caller's latest words are 4 or fewer
    sticky        also keep the top-N tools for the previous turn on its own
    split         split "X aur Y" into parts and route each part
    expand        add each selected tool's `needs` and `next` tools
    always_on     tools included on every turn
    cascade       if the router is unsure, ask the LLM to rewrite the request into intents and route those
    distractors   add N extra tools from neighbouring banking products (scaling test)
"""
import random
import re
import time
from dataclasses import dataclass, field

from router.index import BM25, Embedder, distractor_entries, document, real_entries, tokens_for  # noqa: F401

ALWAYS_ON = ["list_loans", "list_customer_products", "transfer_to_human"]
SPLITTER = re.compile(r"\s*(?:[,?;।]|\b(?:aur|and|also|plus|phir|then|uske baad)\b|\bऔर\b|\bफिर\b)\s*", re.I)


@dataclass
class RouterConfig:
    name: str = "router"
    method: str = "hybrid"
    model: str = "e5-small"
    fusion: str = "weighted"
    alpha: float = 0.7
    expansion: str = "none"
    k: int = 5
    adaptive: bool = False
    history: bool = False
    history_weight: float = 0.5
    short_boost: bool = False
    sticky: int = 0
    split: bool = False
    expand: bool = False
    always_on: list = field(default_factory=list)
    doc_examples: bool = True
    cascade: bool = False
    cascade_margin: float = 0.05     # unsure = top-1 and top-2 scores closer than this (hybrid scale)
    distractors: int = 0
    seed: int = 0


class Router:
    _indexes = {}  # share built indexes between routers with the same documents

    def __init__(self, config: RouterConfig, rewrite_fn=None, entries=None):
        self.c = config
        self.rewrite_fn = rewrite_fn  # text, context -> list of short intents (used by cascade)
        # entries: tools from anywhere (e.g. MCP servers). Default: the local catalog (+ distractors).
        self.entries = list(entries) if entries is not None else real_entries() + distractor_entries(config.distractors)
        self.by_name = {e.name: e for e in self.entries}
        self.names = [e.name for e in self.entries]
        self.rng = random.Random(config.seed)
        with_syn = config.expansion == "append"
        docs = {e.name: document(e, config.doc_examples, with_syn) for e in self.entries}
        key = (config.distractors, config.doc_examples, with_syn)
        if entries is not None:
            key = ("custom",) + tuple(docs[n] for n in self.names)
        if key not in Router._indexes:
            Router._indexes[key] = BM25(docs)
        self.bm25 = Router._indexes[key]
        self.emb = None
        if config.method in ("dense", "hybrid", "area"):
            self.emb = Embedder(config.model)
            self.doc_vecs = self.emb.embed([docs[n] for n in self.names], as_query=False)
            self.multi = None
            if config.expansion == "multi":
                owners, texts = [], []
                for i, e in enumerate(self.entries):
                    for q in e.synthetic + (e.examples if config.doc_examples else []):
                        owners.append(i)
                        texts.append(q)
                self.multi = (self.emb.np.array(owners), self.emb.embed(texts, as_query=True))
            self.emb.save()
        self.cascade_calls = 0

    # ---------- scoring ----------

    def _dense(self, text):
        q = self.emb.embed([text], as_query=True)[0]
        sims = self.doc_vecs @ q
        if self.multi is not None:  # a tool's score is its best vector: document or any of its queries
            owners, vecs = self.multi
            qs = vecs @ q
            best = self.emb.np.full(len(self.names), -1.0, dtype="float32")
            self.emb.np.maximum.at(best, owners, qs)
            sims = self.emb.np.maximum(sims, best)
        return dict(zip(self.names, map(float, sims)))

    def _score(self, text):
        if self.c.method == "random":
            return {n: self.rng.random() for n in self.names}
        if self.c.method == "bm25":
            return self.bm25.scores(text)
        dense = self._dense(text)
        if self.c.method in ("dense", "area"):
            return dense
        sparse = self.bm25.scores(text)
        if self.c.fusion == "rrf":
            def ranks(s):
                return {n: r for r, n in enumerate(sorted(s, key=s.get, reverse=True), 1)}
            rd, rs = ranks(dense), ranks(sparse)
            return {n: 1 / (60 + rd[n]) + 1 / (60 + rs[n]) for n in self.names}
        top = max(sparse.values()) or 1.0
        return {n: self.c.alpha * dense[n] + (1 - self.c.alpha) * sparse[n] / top for n in self.names}

    def _scores_with_history(self, text, context):
        scores = self._score(text)
        if self.c.history and context:
            ctx = self._score(context)
            w = self.c.history_weight * (3 if self.c.short_boost and len(text.split()) <= 4 else 1)
            scores = {n: scores[n] + w * ctx[n] for n in self.names}
        return scores

    # ---------- choosing ----------

    def _rank(self, scores):
        ranked = sorted(scores, key=scores.get, reverse=True)
        if self.c.method == "area":
            area_best = {}
            for n in ranked:
                area_best.setdefault(self.by_name[n].area, scores[n])
            top_areas = sorted(area_best, key=area_best.get, reverse=True)[:2]
            ranked = [n for n in ranked if self.by_name[n].area in top_areas]
        return ranked

    def _top(self, scores, k):
        ranked = self._rank(scores)
        if not self.c.adaptive:
            return ranked[:k]
        lo, hi = 3, min(2 * k, len(ranked))
        vals = [scores[n] for n in ranked[:hi + 1]]
        gaps = [vals[i] - vals[i + 1] for i in range(lo - 1, hi)]
        return ranked[:lo + max(range(len(gaps)), key=gaps.__getitem__)] if gaps else ranked[:k]

    def _unsure(self, scores):
        a, b = sorted(scores.values(), reverse=True)[:2]
        return (a - b) < self.c.cascade_margin

    def select(self, text, context=""):
        """text: the caller's latest words. context: the previous turn (caller + agent), if any.
        Returns tool names, most relevant first."""
        scores = self._scores_with_history(text, context)
        parts = [p for p in SPLITTER.split(text) if len(p.split()) >= 2] if self.c.split else []
        if self.c.cascade and self.rewrite_fn and self._unsure(scores):
            self.cascade_calls += 1
            parts = self.rewrite_fn(text, context) or parts

        if len(parts) > 1 or (self.c.cascade and parts):
            per_part = max(2, self.c.k // max(len(parts), 1) + 1)
            chosen = []
            for p in parts:
                for n in self._top(self._scores_with_history(p, context), per_part):
                    if n not in chosen:
                        chosen.append(n)
            for n in self._top(scores, self.c.k):
                if len(chosen) >= self.c.k + 2:
                    break
                if n not in chosen:
                    chosen.append(n)
        else:
            chosen = self._top(scores, self.c.k)

        if self.c.sticky and context:
            for n in self._top(self._score(context), self.c.sticky):
                if n not in chosen:
                    chosen.append(n)
        if self.c.expand:
            for n in list(chosen):
                for linked in self.by_name[n].needs + self.by_name[n].next:
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
            return [n for n in self.select(users[-1], context) if self.by_name[n].real]
        return select_tools
