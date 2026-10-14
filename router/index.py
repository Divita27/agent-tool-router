"""Searchable descriptions of every tool: text documents, keyword index and embeddings.

Embeddings are cached per tool with a hash of its document, so when a tool is added or its
description changes, only that tool is re-embedded (incremental indexing).
"""
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from tools import TOOLS, schema

CACHE_DIR = Path(__file__).parent / "cache"

# ---------- tool documents ----------

def tool_document(tool, with_examples=True):
    """Text that represents a tool for search. Template from the Semantic Tool Discovery paper,
    with our example phrases as the 'Capabilities' line."""
    params = "; ".join(f"{n}: {p.description}" for n, p in tool.params.items()) or "none"
    lines = [f"Tool: {tool.name.replace('_', ' ')}", f"Purpose: {tool.description}"]
    if with_examples:
        lines.append(f"Capabilities: {' | '.join(tool.examples)}")
    lines.append(f"Parameters: {params}")
    return "\n".join(lines)


# ---------- token cost of exposing a tool ----------

def _tool_tokens():
    """Estimated prompt tokens per tool definition, calibrated on the Step 6 measurement:
    all 111 tools = 11,960 prompt tokens, of which ~65 are the base prompt."""
    chars = {t.name: len(json.dumps(schema(t))) for t in TOOLS}
    per_char = (11_960 - 65) / sum(chars.values())
    return {name: round(c * per_char) for name, c in chars.items()}


TOOL_TOKENS = _tool_tokens()


# ---------- keyword search (BM25) ----------

STOPWORDS = set("""
a an the is are was of to for in on at my me i you your it this that and or do does can please
hai hain ka ki ke ko se me mein mera meri mere mujhe kya kar karo kab kitna kitni hoga hogi tha thi
bhi aur ho ye yeh wo woh na nahi abhi to toh
है हैं का की के को से में मेरा मेरी मेरे मुझे क्या कर करो कब कितना कितनी था थी भी और हो ये वो ना नहीं अभी तो
""".split())

TOKEN = re.compile(r"[a-z0-9ऀ-ॿ]+")


def tokenize(text):
    return [w for w in TOKEN.findall(text.lower().replace("_", " ")) if w not in STOPWORDS and len(w) > 1]


class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.names = list(docs)
        self.tokens = [tokenize(docs[n]) for n in self.names]
        self.k1, self.b = k1, b
        self.avg_len = sum(map(len, self.tokens)) / len(self.tokens)
        df = Counter(w for toks in self.tokens for w in set(toks))
        n = len(self.tokens)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}
        self.tf = [Counter(toks) for toks in self.tokens]

    def scores(self, query):
        q = tokenize(query)
        out = {}
        for name, tf, toks in zip(self.names, self.tf, self.tokens):
            s = 0.0
            for w in q:
                if w in tf:
                    f = tf[w]
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(toks) / self.avg_len))
            out[name] = s
        return out


# ---------- embeddings ----------

MODELS = {
    "e5-small": ("intfloat/multilingual-e5-small", "query: ", "passage: "),
    "minilm": ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "", ""),
}


class Embedder:
    """Embeds tool documents once (cached on disk by hash) and queries on demand."""

    def __init__(self, model_key, docs):
        from sentence_transformers import SentenceTransformer
        import numpy as np

        self.np = np
        model_name, self.q_prefix, self.d_prefix = MODELS[model_key]
        self.model = SentenceTransformer(model_name)
        self.names = list(docs)
        self.vectors = self._index(model_key, docs)

    def _index(self, model_key, docs):
        CACHE_DIR.mkdir(exist_ok=True)
        path = CACHE_DIR / f"{model_key}.json"
        cache = json.loads(path.read_text()) if path.exists() else {}
        hashes = {n: hashlib.sha256(docs[n].encode()).hexdigest()[:16] for n in self.names}
        stale = [n for n in self.names if cache.get(n, {}).get("hash") != hashes[n]]
        if stale:  # only new or changed tools are embedded again
            vecs = self.model.encode([self.d_prefix + docs[n] for n in stale], normalize_embeddings=True)
            for n, v in zip(stale, vecs):
                cache[n] = {"hash": hashes[n], "vector": [round(float(x), 6) for x in v]}
        removed = set(cache) - set(self.names)
        for n in removed:
            del cache[n]
        if stale or removed:
            path.write_text(json.dumps(cache))
        self.reindexed = len(stale)
        return self.np.array([cache[n]["vector"] for n in self.names], dtype="float32")

    def scores(self, query):
        q = self.model.encode([self.q_prefix + query], normalize_embeddings=True)[0]
        sims = self.vectors @ q
        return dict(zip(self.names, map(float, sims)))
