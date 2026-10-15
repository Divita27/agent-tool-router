"""Small wrapper around Sarvam's Chat Completions API.

Every part of the project calls the model through `chat()`, so token counts
and latency are measured the same way everywhere.
"""
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

URL = "https://api.sarvam.ai/v1/chat/completions"
API_KEY = os.environ.get("SARVAM_API_KEY")
DEFAULT_MODEL = os.environ.get("SARVAM_MODEL", "sarvam-105b-conversations")

if not API_KEY:
    raise SystemExit("SARVAM_API_KEY is missing. Copy .env.example to .env and add your key.")


def chat(messages, tools=None, model=DEFAULT_MODEL, tool_choice="auto", timeout=120, retries=3):
    """Send one request. Returns a dict with the reply, tool calls, usage and latency."""
    body = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice

    headers = {"api-subscription-key": API_KEY, "Content-Type": "application/json"}
    start = time.time()
    for attempt in range(retries + 1):
        try:
            r = requests.post(URL, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as e:  # timeout or connection problem
            if attempt == retries:
                raise RuntimeError(f"Sarvam API unreachable: {e}")
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            break
        if (r.status_code == 429 or r.status_code >= 500) and attempt < retries:
            time.sleep(2 ** attempt)  # rate limit or server error: wait 1, 2, 4 s and retry
            continue
        raise RuntimeError(f"Sarvam API error {r.status_code}: {r.text}")
    latency_ms = int((time.time() - start) * 1000)

    data = r.json()
    message = data["choices"][0]["message"]
    return {
        "message": message,
        "content": message.get("content"),
        "tool_calls": message.get("tool_calls") or [],
        "finish_reason": data["choices"][0].get("finish_reason"),
        "usage": data.get("usage") or {},
        "latency_ms": latency_ms,
    }
