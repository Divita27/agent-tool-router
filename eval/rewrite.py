"""LLM intent rewriting for the router's cascade (Re-Invoke's online step, used only when unsure).

rewrite(text, context) -> ["check next EMI date", "check credit card bill"]
Results are cached in eval/data/generated/rewrites.json, so each sentence costs money only once.
"""
import hashlib
import json
import threading
import time

from eval.generate import GEN, parse_json

PROMPT = """A caller to an Indian lending company's phone helpline (loans, credit card, fixed deposits) said:
"{text}"
{context}
List each separate thing the caller wants, as a short English phrase starting with a verb, naming the product
if known (e.g. "check next EMI of personal loan", "block lost credit card"). Use the previous turn to understand
short replies like "haan wahi" or "personal wala". Usually 1 item, at most 3.
Reply with ONLY a JSON list of strings."""

PATH = GEN / "rewrites.json"
_lock = threading.Lock()
_cache = json.loads(PATH.read_text()) if PATH.exists() else {}
stats = {"calls": 0, "cached": 0, "ms": [], "prompt_tokens": 0, "completion_tokens": 0}


def rewrite(text, context=""):
    key = hashlib.sha256(f"{text}|{context}".encode()).hexdigest()[:20]
    if key in _cache:
        stats["cached"] += 1
        return _cache[key]["intents"]
    from agent.sarvam_client import chat
    ctx = f'Previous turn (caller + agent): "{context}"' if context else ""
    for attempt in range(5):
        try:
            r = chat([{"role": "user", "content": PROMPT.format(text=text, context=ctx)}])
            intents = [str(x) for x in parse_json(r["content"])][:3]
            break
        except Exception as e:
            if attempt == 4:
                return []
            time.sleep(2 ** attempt if "429" in str(e) else 1)
    stats["calls"] += 1
    stats["ms"].append(r["latency_ms"])
    stats["prompt_tokens"] += r["usage"].get("prompt_tokens", 0)
    stats["completion_tokens"] += r["usage"].get("completion_tokens", 0)
    with _lock:
        _cache[key] = {"text": text, "context": context, "intents": intents, "latency_ms": r["latency_ms"]}
        PATH.write_text(json.dumps(_cache, ensure_ascii=False, indent=1))
    return intents


def cached_latencies():
    return [v["latency_ms"] for v in _cache.values()]
