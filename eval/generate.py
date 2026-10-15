"""Generate data with sarvam-105b for router experiments. Every result is cached, so reruns are free.

    python -m eval.generate queries      # ~12 synthetic caller queries per tool (Re-Invoke style)
    python -m eval.generate testset      # independent test set: the LLM plays the caller from the scenario cards
    python -m eval.generate distractors  # ~400 extra tools from neighbouring banking products (scaling test)
    python -m eval.generate all --dry-run

Inputs are kept apart on purpose:
  - tool queries see ONLY the tool definition (not our examples or dev sentences)
  - the test set sees ONLY the persona + situation card (never tool names or descriptions)
"""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from eval.scenarios import PERSONAS, SCENARIOS, TODAY_NOTE
from tools import TOOLS

GEN = Path(__file__).parent / "data" / "generated"
PRICE_IN, PRICE_OUT = 15, 60  # ₹ per 1M tokens

STYLES = """Write each item in ONE of these three styles, an equal number of each:
- "hinglish": Hindi-English mix written in Roman letters, e.g. "mera card kho gaya, block kar do"
- "codemix": like speech-to-text output. Hindi words MUST be written in Devanagari script (देवनागरी); only English words stay in Roman letters; little punctuation. e.g. "मेरा card खो गया block कर दो", "personal loan की EMI कब है"
- "english": Indian English, e.g. "I lost my card, please block it\""""

QUERY_PROMPT = """You are helping test a phone helpline for an Indian lending company (personal, home and vehicle loans, credit cards, fixed deposits).

Here is one action the helpline's system can perform:
Name: {name}
Description: {description}
Inputs: {params}

Write 12 different things a real customer might SAY on a phone call that this action would handle.
- Sound like real callers: casual, sometimes indirect, sometimes with a reason or emotion.
- Don't copy the description's wording. Vary vocabulary.
- Don't mention internal IDs unless a caller would naturally say one.
{styles}

Reply with ONLY a JSON list of objects like {{"style": "hinglish", "text": "..."}}."""

TESTSET_PROMPT = """You are role-playing a customer calling an Indian lending company's phone helpline.

Private background (for your understanding only; do NOT recite it, real callers don't):
{persona}
{today}
{history}
What you want to do now: {situation}

Say ONLY what you would say for this request, in at most 25 words, in three versions:
{styles}
Speak naturally, the way people talk on calls. Don't read the situation out. Don't mention loans, EMIs or
products that this request isn't about.

Reply with ONLY JSON: {{"hinglish": "...", "codemix": "...", "english": "..."}}"""

DOMAINS = [
    "gold loans", "loan against property", "education loans", "business loans for shops", "two-wheeler insurance",
    "health insurance", "term life insurance", "mutual fund SIPs", "demat and stock trading account",
    "savings account", "debit card for savings account", "UPI and payments app", "electricity and mobile bill payments",
    "FASTag", "credit score report", "microfinance group loans", "agriculture and tractor loans", "NRI accounts",
    "bank locker", "forex card and travel money", "cheque book and cheques", "recurring deposits",
    "public provident fund (PPF)", "pension (NPS)", "buy now pay later", "consumer durable EMI (phones, TVs)",
    "salary account and payroll", "home insurance", "rewards and cashback programme", "account statements and tax forms",
    "doorstep banking", "senior citizen services", "joint accounts and minors' accounts", "fraud and cyber security",
    "loan against mutual funds", "car insurance claims", "home renovation loans", "wedding loans", "medical loans",
    "credit line / overdraft",
]

DISTRACTOR_PROMPT = """A large Indian bank's phone helpline has many backend actions. Invent 10 realistic actions for this product area: {domain}.

Rules:
- Each action does ONE specific thing (look something up, change something, pay, close, etc).
- Use snake_case names that start with the product, e.g. gold_loan_get_interest_due.
- These are a DIFFERENT product from ordinary personal loans, home loans, car/bike loans, the bank's credit card and fixed deposits, so don't duplicate those.
For each action give: name, a one-sentence description, a list of input parameter names, and 3 short things a caller might say ({styles_short}).

Reply with ONLY a JSON list of objects: {{"name": "...", "description": "...", "params": ["..."], "examples": ["...", "...", "..."]}}"""


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = min([i for i in (text.find("["), text.find("{")) if i >= 0], default=0)
    return json.loads(text[start:])


DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def valid_queries(data):
    items = [x for x in data if isinstance(x, dict) and x.get("text")]
    cm = [x for x in items if x.get("style") == "codemix"]
    return len(items) >= 10 and sum(bool(DEVANAGARI.search(x["text"])) for x in cm) >= 3


def valid_turn(data):
    return (isinstance(data, dict) and all(data.get(k) for k in ("hinglish", "codemix", "english"))
            and DEVANAGARI.search(data["codemix"]) and len(data["english"].split()) <= 35)


def valid_distractors(data):
    return isinstance(data, list) and len(data) >= 8


def ask(prompt, max_tries=6, validate=None):
    import time
    from agent.sarvam_client import chat
    last = None
    for attempt in range(max_tries):
        try:
            r = chat([{"role": "user", "content": prompt}])
            data = parse_json(r["content"])
            if validate and not validate(data):
                raise ValueError("output failed validation")
            return data, r["usage"]
        except Exception as e:  # rate limit, API hiccup or bad JSON: wait and try again
            last = e
            time.sleep(2 ** attempt if "429" in str(e) else 1)  # 1, 2, 4, 8, 16 s on rate limits
    raise RuntimeError(f"gave up after {max_tries} tries: {last}")


def run_jobs(name, jobs, dry_run, est_in, est_out, validate=None):
    """jobs: {key: prompt}. Results cached in GEN/name.json; only missing keys are sent."""
    GEN.mkdir(parents=True, exist_ok=True)
    path = GEN / f"{name}.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    if validate:  # cached items that fail today's validation are generated again
        cache = {k: v for k, v in cache.items() if validate(v)}
    todo = {k: p for k, p in jobs.items() if k not in cache}
    cost = len(todo) * (est_in * PRICE_IN + est_out * PRICE_OUT) / 1e6
    print(f"{name}: {len(cache)} cached, {len(todo)} to generate, about ₹{cost:.1f}")
    if dry_run or not todo:
        return cache
    spent_in = spent_out = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {k: pool.submit(ask, p, validate=validate) for k, p in todo.items()}
        for k, f in futures.items():
            try:
                data, usage = f.result()
                cache[k] = data
                spent_in += usage.get("prompt_tokens", 0)
                spent_out += usage.get("completion_tokens", 0)
            except Exception as e:
                print(f"  {k}: {e}", file=sys.stderr)
            path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    print(f"  done: {len(cache)}/{len(jobs)} cached, spent about ₹{(spent_in * PRICE_IN + spent_out * PRICE_OUT) / 1e6:.2f}")
    return cache


def queries(dry_run):
    jobs = {t.name: QUERY_PROMPT.format(
        name=t.name, description=t.description, styles=STYLES,
        params=", ".join(f"{n} ({p.description})" for n, p in t.params.items()) or "none")
        for t in TOOLS}
    return run_jobs("tool_queries", jobs, dry_run, est_in=350, est_out=750, validate=valid_queries)


def testset(dry_run):
    jobs = {}
    for s in SCENARIOS:
        history = ""
        for i, t in enumerate(s.turns, 1):
            key = f"{s.id}_t{i}"
            jobs[key] = TESTSET_PROMPT.format(persona=PERSONAS[s.customer][1], today=TODAY_NOTE, styles=STYLES,
                                              history=history, situation=t.situation)
            if t.agent_reply:
                history += f"\nSo far in this call: you were asked to: {t.situation}\nThe agent replied: \"{t.agent_reply}\"\n"
    return run_jobs("llm_testset", jobs, dry_run, est_in=450, est_out=150, validate=valid_turn)


def distractors(dry_run):
    jobs = {d: DISTRACTOR_PROMPT.format(domain=d, styles_short="one Hinglish in Roman letters, one Hindi in "
                                        "Devanagari, one English") for d in DOMAINS}
    return run_jobs("distractor_tools", jobs, dry_run, est_in=250, est_out=1600, validate=valid_distractors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("what", choices=["queries", "testset", "distractors", "all"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for fn in (queries, testset, distractors):
        if args.what in (fn.__name__, "all"):
            fn(args.dry_run)


if __name__ == "__main__":
    main()
