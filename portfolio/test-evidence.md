# Test evidence

What each check covers, its latest result, and how to rerun it. Run everything from the project folder with the virtual environment active (setup is in the [project README](../README.md#running-it)).

## Quick checks (no model calls)

| Check | Command | What it covers | Latest result |
|---|---|---|---|
| Backend | `python -m scripts.smoke_backend` | Calls all 111 lending actions with realistic inputs, including cases that should be refused (wrong amount, closed loan, wrong product) | 129/129 cases passed |
| Tool definitions | `python -m scripts.check_tools` | Every tool definition matches its backend function exactly (inputs, required fields), and every link points to a real tool | 111/111 tools match |
| Test scenarios | `python -m scripts.check_scenarios` | All 58 scenarios (71 turns) have valid labels, and labelled answers actually work against the backend | 58 scenarios, 71 turns, all valid |
| Safety layer | `python -m scripts.test_safety` | Confirmations, one yes per action, changed details need a new yes, prerequisites, yes/no in three languages, code-calculated amounts, PII masking, loan-promise filter, audit trail | 41/41 passed |
| MCP | `python -m eval.mcp_check` | Identical picks through MCP on 423 turns, tools added and removed while running, safety through MCP | 15/15 passed |

## Checks that call the model (small cost)

| Check | Command | What it covers | Latest result |
|---|---|---|---|
| MCP, live | `python -m eval.mcp_check --live` | The 15 checks above plus a real conversation through MCP | 17/17 passed, on two separate runs |
| End-to-end benchmark | `python -m eval.e2e_eval --dry-run`, then without `--dry-run` | Full agent on 62 independent turns, several setups. Shows the cost before running and stops at a spending cap | Results in `results/step11/` |

## Experiments (no model calls, rerun from saved data)

| Experiment | Command | Results file |
|---|---|---|
| Building the router layer by layer, choosing K | `python -m eval.router_eval` | `results/step8/ablation.json` |
| Newer techniques, scaling to 511 tools | `python -m eval.research_eval` | `results/step8b/research.json` |
| Re-score the end-to-end runs | `python -m eval.e2e_rescore` | `results/step11/rescored.json` |
| Redraw every chart | `python -m eval.make_charts` | `docs/charts/` |

## Where the numbers come from

Every result in the README and in [results.md](results.md) is read from these saved files, not typed in by hand. The end-to-end raw runs (`results/step11/raw.jsonl`) include every turn's text, the tools called, the reply, token counts and timing, so any number can be traced back to the run that produced it.
