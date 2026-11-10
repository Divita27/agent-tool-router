# All results

Every number here comes from a saved file in `results/`. The file is named under each section.

Two test sets are used for the router comparisons:

- **Dev set:** 213 turns I typed (71 scenario turns in three styles: Hinglish, Devanagari + English, English). I used this while building.
- **Independent set:** 210 turns written by `sarvam-105b` playing the caller from the same scenario cards, without seeing tool names or my sentences. This is the fairer test.

"Every needed tool picked" means every tool the turn required was in the list sent to the model.

---

## 1. Tokens and speed vs number of tools

`results/step6/summary.json` · 120 model calls, 24 per size, same 8 questions.

| Tools sent | Prompt tokens | Share of 32K window | Median latency | p95 latency | Right tool |
|---|---|---|---|---|---|
| 5 | 586 | 1.8% | 267 ms | 322 ms | 24/24 |
| 10 | 1,141 | 3.6% | 242 ms | 328 ms | 24/24 |
| 25 | 2,790 | 8.7% | 288 ms | 368 ms | 24/24 |
| 50 | 5,366 | 16.8% | 350 ms | 541 ms | 24/24 |
| 111 | 11,960 | 37.4% | 336 ms | 397 ms | 24/24 |

About 107 tokens per tool, growing in a straight line. A single call barely slows down; the cost is in tokens.

---

## 2. Building the router, one layer at a time

`results/step8/ablation.json` · dev set, K=5.

| Configuration | Every needed tool picked | Tools sent | Tool tokens |
|---|---|---|---|
| Random | 7.0% | 5 | 539 |
| Word matching (BM25) | 90.6% | 5 | 545 |
| Meaning matching, MiniLM | 72.3% | 5 | 575 |
| Meaning matching, e5-small | 82.6% | 5 | 575 |
| e5-small without example phrases | 74.6% | 5 | 556 |
| Hybrid, 70% meaning + 30% words | 92.0% | 5 | 549 |
| Hybrid, 50/50 | 91.5% | 5 | 547 |
| Topic first, then tools | 81.7% | 5 | 571 |
| Hybrid + previous turn | 93.4% | 5 | 548 |
| + sentence splitting | 92.5% | 5.2 | 569 |
| + linked tools | 94.8% | 6.3 | 677 |
| + always-on tools | 94.8% | 9.2 | 952 |
| + adaptive K | 95.3% | 10.0 | 1,039 |
| + short-reply boost (with splitting) | 95.8% | 9.3 | 952 |
| **+ short-reply boost, no splitting** | **97.2%** | **9.1** | **930** |

Linked tools rescued 5 of 213 turns and broke none.

---

## 3. Choosing K

`results/step8/ablation.json` (sweep) · dev set, without the three always-on tools.

| K | Word matching | Meaning matching | Full router |
|---|---|---|---|
| 1 | 71.8% | 64.3% | 79.3% |
| 2 | 83.1% | 74.6% | 88.3% |
| 3 | 87.8% | 78.9% | 92.0% |
| 5 | 90.6% | 82.6% | 97.2% |
| 8 | 94.4% | 88.3% | 97.7% |
| 12 | 94.8% | 92.0% | 98.6% |
| 20 | 96.2% | 93.4% | 99.5% |

---

## 4. Newer techniques, on both test sets

`results/step8b/research.json`

Retrieval only, K=5:

| Technique | Dev | Independent | Tool tokens |
|---|---|---|---|
| Word matching | 90.6% | 93.3% | 538 |
| + generated example queries | 88.7% | 91.9% | 547 |
| Meaning matching | 82.6% | 86.2% | 569 |
| + generated queries, appended | 84.5% | 88.1% | 560 |
| + generated queries, separate vectors | 83.6% | 82.4% | 533 |
| Hybrid, weighted | 92.0% | 92.9% | 541 |
| Hybrid, rank fusion (RRF) | 88.7% | 91.9% | 551 |
| Hybrid + separate vectors | 93.0% | 93.8% | 537 |

Full router (previous turn, short-reply boost, linked tools, always-on):

| Variant | Dev | Independent | Tool tokens |
|---|---|---|---|
| K=5 | 97.2% | 96.7% | 930 |
| K=5 + generated queries | 97.2% | 96.2% | 928 |
| K=5 + generated queries, RRF | 94.8% | 95.2% | 926 |
| K=5 + generated queries + sticky tools | 97.2% | 97.6% | 944 |
| K=3 + generated queries | 92.5% | 92.4% | 687 |
| K=8 + generated queries | 98.6% | 98.6% | 1,300 |

Asking the model to rewrite the request when the router is unsure (K=8 + generated queries):

| When to rewrite | Dev | Independent | Tool tokens | Turns calling the model |
|---|---|---|---|---|
| Top two scores within 0.02 | 98.1% | 98.6% | 1,317 | 11.4% |
| Within 0.05 | 98.6% | 98.6% | 1,334 | 23.3% |
| Within 0.10 | 98.6% | 99.0% | 1,391 | 52.4% |
| Always | 98.6% | 98.1% | 1,457 | 100% |

The rewrite call itself took about 233 ms typically and 621 ms at worst.

---

## 5. Bigger catalogs

`results/step8b/research.json` (scaling) · independent set. 400 extra tools from 40 neighbouring banking products.

| Method | 111 tools | 311 tools | 511 tools |
|---|---|---|---|
| Word matching | 93.3% | 88.6% | 84.8% |
| Meaning matching | 86.2% | 81.4% | 81.4% |
| Router, K=5 | 96.7% | 92.4% | 91.4% |
| Router, K=8 + generated queries | 98.6% | 96.7% | 96.2% |

---

## 6. The full agent, end to end

`results/step11/rescored.json` · full agent with the safety layer and `sarvam-105b-conversations`, 62 independent turns in codemix (Devanagari + English), all setups on the same turns.

| Setup | Handled well | Acted this turn | Right details | Prompt tokens / turn | Model calls / turn | Typical turn | p95 turn | ₹ per 1,000 turns | Asked caller for an ID | Wrong actions |
|---|---|---|---|---|---|---|---|---|---|---|
| All 111 tools | 96.8% | 74.2% | 93.8% | 31,442 | 2.52 | 1.38 s | 2.55 s | 477 | 3 | 1 |
| Router, K=5 | 88.7% | 64.5% | 91.7% | 3,484 | 2.31 | 1.26 s | 2.29 s | 57 | 9 | 0 |
| Router, K=8 | 93.5% | 67.7% | 96.0% | 4,328 | 2.29 | 0.98 s | 2.23 s | 70 | 5 | 0 |

"Handled well" means the agent called the right tool, or asked a sensible question ("which loan?", "shall I go ahead?") without doing anything wrong and without asking the caller for an internal ID.

---

## 7. Tools served over MCP

`results/mcp/check.json` · 17 of 17 checks passed, on two separate runs.

| Check | Result |
|---|---|
| Connect to five servers | 1.2 s; loans 37, payments 24, customer 31, cards 12, deposits 7 = 111 tools |
| Same picks as the in-process router | Identical tool lists on all 423 turns, at K=5 and K=8 |
| Tool added to a running server | Announced in about 0.5 s, one new embedding, routable and callable on the next turn |
| Tool removed | Gone after the server's notification |
| Safety through MCP | Irreversible tool held until "yes"; tools with no risk level need a yes; phone numbers masked |
| Live conversation | Picked `get_next_emi`; 13 tools sent; about 4,100–4,300 prompt tokens per turn; no errors |
