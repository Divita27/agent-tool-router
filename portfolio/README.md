# Portfolio guide

If you're reviewing this project, this page is the quickest way through it.

## The project in two minutes

An AI agent that can use 100+ tools has a hidden cost: every time the model thinks, it is sent the description of every tool. In this project that was about 12,000 tokens per call for 111 tools, and one turn of a conversation usually takes two or three calls.

I built a lending-company agent on Sarvam's `sarvam-105b-conversations` (loans, EMIs, cards, deposits, KYC: 111 tools, Hindi/Hinglish/English) and a router that shortlists about 12 tools per turn without calling a model. The model then picks from the shortlist. A separate safety layer decides in code whether a risky tool may run. The tools can also be served from five MCP servers, and the router discovers them, including tools added while it's running.

The main results, on the full agent:

| | All 111 tools | Router (K=8) |
|---|---|---|
| Turns handled well | 96.8% | 93.5% |
| Prompt tokens per turn | 31,442 | 4,328 (86% fewer) |
| Typical turn time | 1.38 s | 0.98 s |
| Wrong actions | 1 | 0 |

## What to look at first

| If you have | Look at |
|---|---|
| 2 minutes | The [project README](../README.md): headline chart, architecture diagram and highlights |
| 10 minutes | [walkthroughs.md](walkthroughs.md): real runs, step by step, including the router pulling the right tool from rank 66 to rank 7 and the safety layer catching a wrong payment amount |
| 20 minutes | [results.md](results.md): every result table from every test |
| Interested in how I work | [lessons-learned.md](lessons-learned.md): what went wrong and what I changed |
| Want to check it yourself | [test-evidence.md](test-evidence.md): what each test suite checks and how to rerun it |

## Where things are in the code

| Folder | What's there |
|---|---|
| `router/` | The router (`router.py`, `index.py`) |
| `agent/` | The conversation loop and the Sarvam client |
| `safety/` | Confirmations, yes/no in three languages, PII masking |
| `mcp_servers/` | Five MCP servers and the hub (MCP client) that connects the router to them |
| `tools/` | The 111 tool definitions with risk levels, examples and links |
| `backend/` | The fake lending company the tools act on |
| `eval/` | Test scenarios, generated test sets and every experiment script |
| `results/` | Saved results that every number in these documents comes from |
| `docs/charts/` | The architecture diagram and the result charts |

## Charts

- [Architecture](../docs/charts/architecture.png): how a turn flows, including MCP tool discovery and tool calls
- [Headline results](../docs/charts/headline_end_to_end.png): tokens, quality and cost per setup, end to end
- [Tokens vs tools](../docs/charts/tokens_vs_tools.png): tool definitions filling the 32K context window
- [Technique ladder](../docs/charts/technique_ladder.png): how each technique did
- [Choosing K](../docs/charts/k_sweep.png): why 8 tools are kept
- [Scaling](../docs/charts/scaling.png): 111 to 511 tools
