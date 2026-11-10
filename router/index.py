"""Searchable descriptions of every tool: text documents, keyword index and embeddings.

Embeddings are cached on disk per text (keyed by a hash), so when a tool is added or its
description changes, only the changed text is embedded again (incremental indexing).
"""
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from tools import TOOLS, schema

CACHE_DIR = Path(__file__).parent / "cache"
GENERATED = Path(__file__).parent.parent / "eval" / "data" / "generated"


# ---------- searchable tool entries ----------

class Entry:
    """One searchable tool: our real tools, or a distractor from the scaling test."""

    def __init__(self, name, description, params, examples, synthetic=None, area=None, real=True):
        self.name, self.description, self.params = name, description, params
        self.examples, self.synthetic = examples, synthetic or []
        self.area, self.real = area, real
        self.needs, self.next = [], []


def real_entries():
    queries = _load(GENERATED / "tool_queries.json")
    out = []
    for t in TOOLS:
        e = Entry(t.name, t.description, {n: p.description for n, p in t.params.items()}, t.examples,
                  [q["text"] for q in queries.get(t.name, []) if isinstance(q, dict) and q.get("text")], t.area)
        e.needs, e.next = t.needs, t.next
        out.append(e)
    return out


def distractor_entries(n):
    """First n distractor tools (neighbouring banking products), in a fixed order."""
    data = _load(GENERATED / "distractor_tools.json")
    items = [x for domain in sorted(data) for x in data[domain] if isinstance(x, dict) and x.get("name")]
    return [Entry(x["name"], x.get("description", ""), {p: p for p in x.get("params", [])}, x.get("examples", []),
                  area="distractor", real=False) for x in items[:n]]


def _load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def document(e, with_examples=True, with_synthetic=False):
    """Text that represents a tool for search. Template from the Semantic Tool Discovery paper;
    'Capabilities' holds example phrases (ours) and optionally generated caller queries (Re-Invoke)."""
    params = "; ".join(f"{n}: {d}" for n, d in e.params.items()) or "none"
    lines = [f"Tool: {e.name.replace('_', ' ')}", f"Purpose: {e.description}"]
    caps = (e.examples if with_examples else []) + (e.synthetic if with_synthetic else [])
    if caps:
        lines.append(f"Capabilities: {' | '.join(caps)}")
    lines.append(f"Parameters: {params}")
    return "\n".join(lines)


# ---------- token cost of exposing a tool ----------

def _tool_tokens():
    """Prompt tokens per tool definition, calibrated on Step 6: all 111 tools = 11,960 tokens, ~65 base."""
    chars = {t.name: len(json.dumps(schema(t))) for t in TOOLS}
    per_char = (11_960 - 65) / sum(chars.values())
    return {name: round(c * per_char) for name, c in chars.items()}


TOOL_TOKENS = _tool_tokens()
AVG_TOOL_TOKENS = round(sum(TOOL_TOKENS.values()) / len(TOOL_TOKENS))


def tokens_for(names):
    return sum(TOOL_TOKENS.get(n, AVG_TOOL_TOKENS) for n in names)


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
    "e5-ft": (str(Path(__file__).parent / "models" / "e5-small-lending"), "query: ", "passage: "),
}


class Embedder:
    """Embeds texts with a disk cache keyed by hash, so each text is embedded only once ever."""
    _models = {}
    newly_embedded = 0  # texts actually sent to the model (cache misses), for checking incremental indexing
    newly_embedded_docs = 0  # of which tool documents

    def __init__(self, model_key):
        import numpy as np
        from sentence_transformers import SentenceTransformer

        self.np = np
        self.key = model_key
        path, self.q_prefix, self.d_prefix = MODELS[model_key]
        if path not in Embedder._models:
            Embedder._models[path] = SentenceTransformer(path)
        self.model = Embedder._models[path]
        CACHE_DIR.mkdir(exist_ok=True)
        self.cache_path = CACHE_DIR / f"{model_key}.json"
        self.cache = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self.dirty = False

    def embed(self, texts, as_query):
        prefix = self.q_prefix if as_query else self.d_prefix
        keys = [hashlib.sha256((prefix + t).encode()).hexdigest()[:20] for t in texts]
        missing = [(k, prefix + t) for k, t in zip(keys, texts) if k not in self.cache]
        Embedder.newly_embedded += len(missing)
        if not as_query:
            Embedder.newly_embedded_docs += len(missing)
        if missing:
            vecs = self.model.encode([t for _, t in missing], normalize_embeddings=True, batch_size=64)
            for (k, _), v in zip(missing, vecs):
                self.cache[k] = [round(float(x), 5) for x in v]
            self.dirty = True
        return self.np.array([self.cache[k] for k in keys], dtype="float32")

    def save(self):
        if self.dirty:
            self.cache_path.write_text(json.dumps(self.cache))
            self.dirty = False
