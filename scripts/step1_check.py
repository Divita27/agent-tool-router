"""Step 1: check Sarvam facts the project depends on.

Run:  python step1_check.py
Needs: SARVAM_API_KEY set in your terminal, and `pip install requests`.
"""
import json
import os
import time

import requests

URL = "https://api.sarvam.ai/v1/chat/completions"
HEADERS = {
    "api-subscription-key": os.environ["SARVAM_API_KEY"],
    "Content-Type": "application/json",
}
SYSTEM = "You are a loan support agent for a lending company. Use tools when needed. Reply briefly."
QUESTION = "Mera loan number L123 hai. Agli EMI kab due hai aur kitni hai?"


def make_tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


# 3 realistic tools
REAL_TOOLS = [
    make_tool(
        "get_next_emi",
        "Get the next EMI due date and amount for a customer's loan",
        {"loan_id": {"type": "string", "description": "Loan ID, e.g. L123"}},
        ["loan_id"],
    ),
    make_tool(
        "send_payment_link",
        "Send a payment link to the customer's phone for an overdue EMI",
        {
            "loan_id": {"type": "string", "description": "Loan ID, e.g. L123"},
            "amount": {"type": "number", "description": "Amount in rupees"},
        },
        ["loan_id", "amount"],
    ),
    make_tool(
        "transfer_to_human",
        "Transfer the call to a human support agent",
        {"reason": {"type": "string", "description": "Why the caller needs a human"}},
        ["reason"],
    ),
]

# 40 filler tools, only used to make the prompt bigger
FILLER_TOOLS = [
    make_tool(
        f"service_request_{i}",
        f"Raise service request type {i} for fixed deposits, credit cards or account maintenance",
        {
            "customer_id": {"type": "string", "description": "Customer ID"},
            "note": {"type": "string", "description": "Short note about the request"},
        },
        ["customer_id"],
    )
    for i in range(40)
]


def call(model, tools):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": QUESTION},
        ],
        "tools": tools,
        "tool_choice": "auto",
    }
    start = time.time()
    r = requests.post(URL, headers=HEADERS, json=body, timeout=120)
    ms = int((time.time() - start) * 1000)
    if r.status_code != 200:
        print(f"ERROR {r.status_code}: {r.text}")
        return None
    data = r.json()
    return ms, data["choices"][0], data.get("usage")


def show(label, result):
    print(f"\n=== {label} ===")
    if result is None:
        return None
    ms, choice, usage = result
    print("latency_ms   :", ms)
    print("finish_reason:", choice.get("finish_reason"))
    tool_calls = choice["message"].get("tool_calls") or []
    for tc in tool_calls:
        print("tool called  :", tc["function"]["name"], tc["function"]["arguments"])
    if not tool_calls:
        print("text reply   :", choice["message"].get("content"))
    print("usage (full) :", json.dumps(usage, indent=2))
    return usage


# Test A: does tool calling work on both models?
for model in ["sarvam-105b-conversations", "sarvam-105b"]:
    show(f"A. Tool calling on {model} (3 tools)", call(model, REAL_TOOLS))

# Test B: how many prompt tokens do the tools cost?
small = show("B1. 3 tools", call("sarvam-105b-conversations", REAL_TOOLS))
big = show("B2. 43 tools", call("sarvam-105b-conversations", REAL_TOOLS + FILLER_TOOLS))
if small and big:
    per_tool = (big["prompt_tokens"] - small["prompt_tokens"]) / len(FILLER_TOOLS)
    print(f"\n>>> Roughly {per_tool:.0f} prompt tokens per tool (simple tools; real ones will be bigger)")

# Test C: caching. Same big request 3 times. Look for any 'cached' field in usage
# and check whether latency drops on repeats.
for i in range(1, 4):
    show(f"C{i}. Same 43-tool request, repeat {i}", call("sarvam-105b-conversations", REAL_TOOLS + FILLER_TOOLS))

print("\nDone. Copy the important numbers into NOTES.md.")
