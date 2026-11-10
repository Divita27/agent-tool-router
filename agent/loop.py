"""The agent loop: caller's words -> model -> tools -> short spoken reply.

    agent = Agent(customer_id="C001")
    turn = agent.respond("Meri agli EMI kab hai?")
    print(turn.reply)

Which tools the model sees on each turn is decided by `select_tools`. By default
it's all 111 (the baseline). The router in Step 8 replaces it.
"""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agent.sarvam_client import chat
from safety.layer import SafetyLayer
from safety.pii import mask_for_log
from backend.db import TODAY, db
from tools import TOOLS, schemas

MAX_MODEL_CALLS_PER_TURN = 5
LOG_DIR = Path(__file__).parent.parent / "results" / "logs"

SYSTEM_PROMPT = """You are a phone support agent for Demo Lending Co., a lending company in India.

Today is {today}.

Call context (already known, don't ask the caller for these):
- Caller's phone number: {phone}
- Customer ID: {customer_id}. Use this directly for any tool that needs a customer_id.
- To find the caller's loans, cards or deposits, use their customer ID (e.g. list_loans).

How to talk:
- This is a voice call. Keep replies to one or two short sentences.
- Reply in the caller's language and style: Hindi, English or Hinglish.
- Say amounts the way people speak, e.g. "13,538 rupees". Say dates like "5 November".
- Never read out IDs, long numbers or JSON unless the caller asks.

How to work:
- Use tools to get facts. Never guess amounts, dates or loan details.
- If the caller has more than one loan or card and it's not clear which one they mean, ask.
- If a tool returns an error, explain it simply and offer what you can do next.
- Loan eligibility is always indicative. Never promise that a loan will be approved.

Safety (enforced by the system):
- Actions that change something, move money or touch personal details need the caller's clear yes.
  You may ask "shall I go ahead?" yourself; for payments always say the exact amount in that question.
  If a tool returns "confirmation_required", read its read_back to the caller in simple words (use its
  exact amount) and ask them to confirm. After they say yes, call the SAME tool again with the SAME
  arguments. If they say no, don't call it.
- If a tool returns "prerequisite_missing", do that step first and tell the caller the result.
- Never read out full phone numbers, account numbers or PAN. Say "ending 4821"."""


def all_tools(history):
    return [t.name for t in TOOLS]


@dataclass
class ModelCall:
    tools_offered: int
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    tool_calls: list = field(default_factory=list)   # [{"name", "arguments", "ok", "error", "latency_ms"}]


@dataclass
class Turn:
    user: str
    reply: str = ""
    model_calls: list = field(default_factory=list)
    tools_offered: list = field(default_factory=list)
    total_ms: int = 0
    gave_up: bool = False
    confirmation: str = None     # caller's answer to a pending confirmation: yes / no / unclear
    error: str = None

    @property
    def tools_called(self):
        return [c["name"] for m in self.model_calls for c in m.tool_calls]

    @property
    def prompt_tokens(self):
        return sum(m.prompt_tokens for m in self.model_calls)

    @property
    def completion_tokens(self):
        return sum(m.completion_tokens for m in self.model_calls)


class Agent:
    def __init__(self, customer_id, select_tools=all_tools, executor_mode="local", log=True, audit=True,
                 states=None, tool_schemas=schemas, safety_tools=None, run_tool=None):
        self.customer_id = customer_id
        self.states = states  # optional LiveStates: hand-built states with LLM-driven transitions
        if states:
            select_tools = states.select_tools
        self.safety = SafetyLayer(customer_id, executor_mode, audit=audit, tools=safety_tools, run=run_tool)
        self.tool_schemas = tool_schemas  # names -> function-calling definitions (catalog by default, or MCP)
        self.select_tools = select_tools
        self.executor_mode = executor_mode
        with db() as conn:
            phone = conn.execute("SELECT phone FROM customers WHERE id=?", (customer_id,)).fetchone()["phone"]
        self.messages = [{"role": "system",
                          "content": SYSTEM_PROMPT.format(today=TODAY.strftime("%d %B %Y"), customer_id=customer_id,
                                                          phone=phone)}]
        self.turns = []
        self.log_path = None
        if log:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.log_path = LOG_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{customer_id}.jsonl"

    def respond(self, user_text):
        start = time.time()
        turn = Turn(user=user_text)
        turn.confirmation = self.safety.on_user_turn(user_text)  # did they just answer a "confirm?"
        self.messages.append({"role": "user", "content": user_text})
        turn.tools_offered = self.select_tools(self.messages)
        tool_schemas = self.tool_schemas(turn.tools_offered) + (self.states.transition_schemas() if self.states else [])

        for _ in range(MAX_MODEL_CALLS_PER_TURN):
            try:
                r = chat(self.messages, tools=tool_schemas or None)
            except RuntimeError as e:  # API down even after retries: say something useful, don't crash the call
                turn.error = str(e)[:200]
                turn.reply = ("Maaf kijiye, abhi system mein thodi dikkat hai. Main aapko thodi der mein call back "
                              "karwata hoon.")
                self.messages.append({"role": "assistant", "content": turn.reply})
                break
            call = ModelCall(tools_offered=len(tool_schemas),
                             prompt_tokens=r["usage"].get("prompt_tokens", 0),
                             completion_tokens=r["usage"].get("completion_tokens", 0),
                             latency_ms=r["latency_ms"])
            turn.model_calls.append(call)

            if not r["tool_calls"]:
                turn.reply = self.safety.check_reply((r["content"] or "").strip())
                self.messages.append({"role": "assistant", "content": turn.reply})
                break

            # The model wants tools: run each one and give it the results
            self.messages.append({"role": "assistant", "content": r["content"] or "",
                                  "tool_calls": r["tool_calls"]})
            for tc in r["tool_calls"]:
                name, arguments = tc["function"]["name"], tc["function"]["arguments"]
                if self.states and self.states.handles(name):  # state switch: new tools for the next model call
                    result = self.states.switch(name)
                    call.tool_calls.append({"name": name, "arguments": arguments, "ok": True, "error": None,
                                            "latency_ms": 0})
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})
                    turn.tools_offered = self.select_tools(self.messages)
                    tool_schemas = self.tool_schemas(turn.tools_offered) + self.states.transition_schemas()
                    continue
                outcome = self.safety.execute(name, arguments)
                call.tool_calls.append({"name": name, "arguments": arguments, "ok": outcome["ok"],
                                        "error": outcome.get("error"), "latency_ms": outcome["latency_ms"]})
                content = outcome["result"] if outcome["ok"] else {
                    k: outcome[k] for k in ("error", "message", "read_back") if k in outcome}
                self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                      "content": json.dumps(content, ensure_ascii=False)})
        else:
            turn.gave_up = True
            turn.reply = "Maaf kijiye, abhi main yeh nahi kar pa raha. Main aapko ek agent se connect karta hoon."
            self.messages.append({"role": "assistant", "content": turn.reply})

        self.safety.last_agent_message = turn.reply
        turn.total_ms = int((time.time() - start) * 1000)
        self.turns.append(turn)
        self._log(turn)
        return turn

    def _log(self, turn):
        if not self.log_path:
            return
        record = {
            "time": datetime.now().isoformat(timespec="seconds"), "customer_id": self.customer_id,
            "user": mask_for_log(turn.user), "reply": mask_for_log(turn.reply), "tools_offered": len(turn.tools_offered),
            "tools_called": turn.tools_called, "prompt_tokens": turn.prompt_tokens,
            "completion_tokens": turn.completion_tokens, "model_calls": len(turn.model_calls),
            "total_ms": turn.total_ms, "gave_up": turn.gave_up, "confirmation": turn.confirmation,
            "error": turn.error,
            "steps": [vars(m) for m in turn.model_calls],
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
