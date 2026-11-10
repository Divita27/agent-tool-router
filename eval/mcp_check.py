"""Does the router work with tools served over MCP? Pass marks were fixed before running.

Run from the project folder:  python -m eval.mcp_check          (add --live for the paid model check)

1. Same picks: router fed by MCP picks IDENTICAL tool lists to the catalog router, 423 turns, K=5 and K=8.
2. Live discovery: a tool added to a running server is announced, re-indexed alone, and routable.
3. Safety through MCP: risky tools are held until a yes; unknown new tools need a yes by default.
4. (--live) Real conversation through MCP picks the right tool. Costs well under ₹0.5.
"""
import contextlib
import io
import json
import sys
import time
from pathlib import Path

from backend import seed
from backend.db import db
from eval.router_eval import load
from mcp_servers.hub import ALL_GROUPS, MCPHub, routed_selector
from router.index import Embedder
from router.router import ALWAYS_ON, Router, RouterConfig
from safety.layer import SafetyLayer

EXTRA = Path("mcp_servers/extra/cards.json")
FULL = dict(method="hybrid", history=True, short_boost=True, expand=True, always_on=ALWAYS_ON)
NEW_TOOL = [{"name": "freeze_card_temporarily",
             "description": "Temporarily freeze a credit card for a few days, and unfreeze it later.",
             "params": {"card_id": "Credit card ID, e.g. CC2001", "days": "How many days to freeze it for"},
             "examples": ["card ko kuch din ke liye freeze kar do", "कार्ड को कुछ दिन के लिए रोक दो",
                          "pause my card for a while"],
             "response": {"status": "frozen", "note": "demo tool added at runtime"}}]
results = {}


def check(name, ok, detail=""):
    results[name] = bool(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def quiet_seed():
    with contextlib.redirect_stdout(io.StringIO()):
        seed.main()


def card_status():
    with db() as conn:
        return conn.execute("SELECT status FROM credit_cards WHERE id='CC2001'").fetchone()[0]


def wait_for_change(hub, version, seconds=8):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if hub.version != version:
            return True
        time.sleep(0.1)
    return False


def main():
    quiet_seed()
    EXTRA.unlink(missing_ok=True)
    start = time.time()
    hub = MCPHub(ALL_GROUPS).start()
    try:
        counts = {g: len(hub.tools[g]) for g in ALL_GROUPS}
        print(f"Connected to {len(counts)} MCP servers in {time.time() - start:.1f}s: {counts} "
              f"= {sum(counts.values())} tools")

        print("\n1. Same picks through MCP")
        for k in (5, 8):
            local = Router(RouterConfig(k=k, **FULL))
            via_mcp = Router(RouterConfig(k=k, **FULL), entries=hub.entries())
            for label, path in (("dev", "eval/data/dev_typed.jsonl"), ("independent", "eval/data/llm_test.jsonl")):
                rows = load(path, None)
                same = sum(local.select(r["text"], r["context"]) == via_mcp.select(r["text"], r["context"])
                           for r in rows)
                check(f"K={k} {label}: identical tool lists", same == len(rows), f"{same}/{len(rows)}")

        print("\n2. Live discovery")
        selector = routed_selector(hub, RouterConfig(k=8, **FULL))
        selector([{"role": "user", "content": "hello"}])
        before_docs, version = Embedder.newly_embedded_docs, hub.version
        t0 = time.time()
        EXTRA.parent.mkdir(exist_ok=True)
        new_tool = [dict(NEW_TOOL[0], description=NEW_TOOL[0]["description"] + f" (added at {time.time():.0f})")]
        EXTRA.write_text(json.dumps(new_tool, ensure_ascii=False))
        announced = wait_for_change(hub, version)
        check("server announced the new tool", announced, f"after {time.time() - t0:.1f}s")
        check("hub lists it", "freeze_card_temporarily" in hub.all_tools())
        picked = selector([{"role": "user", "content": "Mera card do din ke liye band rakhna hai, freeze kar do"}])
        new_docs = Embedder.newly_embedded_docs - before_docs
        check("routable on the next turn", "freeze_card_temporarily" in picked, f"picked {picked[:4]}...")
        check("only the new tool was embedded", new_docs == 1, f"{new_docs} new document embedding(s)")
        out = hub.call("freeze_card_temporarily", {"card_id": "CC2001", "days": "2"})
        check("callable through MCP", out["ok"] and out["result"].get("status") == "frozen", str(out.get("result")))

        print("\n3. Safety through MCP")
        quiet_seed()
        s = SafetyLayer("C001", audit=False, tools=hub.all_tools, run=hub.call)
        out = s.execute("block_card", '{"card_id": "CC2001", "reason": "lost"}')
        check("irreversible tool held without a yes", out.get("error") == "confirmation_required"
              and card_status() == "active")
        s.on_user_turn("haan kar do")
        out = s.execute("block_card", '{"card_id": "CC2001", "reason": "lost"}')
        check("runs on the MCP server after yes", out["ok"] and card_status() == "blocked")
        check("read tool runs straight away", s.execute("get_next_emi", '{"loan_id": "L1001"}')["ok"])
        out = s.execute("freeze_card_temporarily", '{"card_id": "CC2001", "days": "2"}')
        check("new tool with no risk level needs a yes", out.get("error") == "confirmation_required")
        r = s.execute("get_registered_contact_details", '{"customer_id": "C001"}')
        s.on_user_turn("yes")
        r = s.execute("get_registered_contact_details", '{"customer_id": "C001"}')
        check("private data masked on results from MCP", r["ok"] and r["result"]["phone"] == "XXXXXX0001",
              r.get("result", {}).get("phone"))

        version = hub.version
        EXTRA.unlink()
        check("removed tool disappears", wait_for_change(hub, version)
              and "freeze_card_temporarily" not in hub.all_tools())

        if "--live" in sys.argv:
            print("\n4. Real conversation through MCP (sarvam-105b-conversations)")
            from agent.loop import Agent
            quiet_seed()
            agent = Agent("C001", select_tools=routed_selector(hub, RouterConfig(k=8, **FULL)),
                          tool_schemas=hub.schemas, safety_tools=hub.all_tools, run_tool=hub.call,
                          log=False, audit=False)
            called = []
            for text in ["Agli EMI kab hai?", "Personal wala"]:
                turn = agent.respond(text)
                called += turn.tools_called
                print(f"  You: {text}\n  Agent: {turn.reply}\n  tools sent {len(turn.tools_offered)}, "
                      f"called {turn.tools_called}, {turn.prompt_tokens:,} prompt tokens")
            check("picked get_next_emi through MCP", "get_next_emi" in called)
            check("no errors", all(t.error is None for t in agent.turns))
    finally:
        EXTRA.unlink(missing_ok=True)
        hub.stop()
        quiet_seed()

    passed = sum(results.values())
    print(f"\n{passed}/{len(results)} checks passed -> {'ALL PASS' if passed == len(results) else 'NOT PASSING'}")
    Path("results/mcp").mkdir(parents=True, exist_ok=True)
    Path("results/mcp/check.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
