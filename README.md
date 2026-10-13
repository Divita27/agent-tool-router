# Loan Agent Router

Routing 100+ tools for a Sarvam voice agent in lending: give the model only the tools it needs on each turn, and measure what that does to cost, context space, accuracy and latency.

> Work in progress. Results below will be filled in from real measurements only.

## Results

| Setup | Tool accuracy | Tool tokens / turn | Cost / 1,000 calls | Latency p50 / p95 |
|---|---|---|---|---|
| A. All tools, no cache | | | | |
| B. All tools, cached (calculated) | | | | |
| C. Hand-designed states | | | | |
| D. Router, top-k | | | | |

## Where it fails

_To be written after the benchmark._

## Project layout

| Folder | What's in it |
|---|---|
| `backend/` | Fake lending company API (FastAPI + SQLite) |
| `tools/` | The ~100 tool definitions |
| `router/` | Picks the top-k tools for each turn |
| `agent/` | The agent loop and the Sarvam API client |
| `safety/` | Confirmations, PII masking, RBI rules |
| `eval/` | Test data and benchmark scripts |
| `results/` | Benchmark output and charts |
| `scripts/` | One-off checks |

## Setup

```bash
python3 -m venv ~/.venvs/loan-agent
source ~/.venvs/loan-agent/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then put your Sarvam key in .env
python -m scripts.hello_sarvam
```
