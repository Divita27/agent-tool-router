"""The safety layer: decides, in code, whether a tool call may run.

The model can ASK for any tool. This layer sits between the model and the backend:

    read          runs immediately
    write / pii   runs only after the caller says yes to a read-back of exactly this action
    payment       same, and the read-back states the amount
    irreversible  same, AND its prerequisite (e.g. the foreclosure quote) must have run in this call

A confirmation covers ONE action with the SAME arguments. If the model changes anything
(amount, loan, account) after the caller said yes, the caller is asked again.

To avoid asking twice, a yes to the AGENT's own question also counts, but only:
    write / pii   if the agent's last message was a question
    payment       if that question stated the exact amount the backend will charge
    irreversible  never: always the system's read-back, with the amount computed by code

Every risky step is written to results/audit.jsonl with sensitive data masked.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from agent.executor import execute
from backend.handlers import HANDLERS, ToolError
from safety.confirm import classify
from safety.pii import mask_for_log, mask_for_model
from tools import TOOLS_BY_NAME

AUDIT = Path(__file__).parent.parent / "results" / "audit.jsonl"
GATED = {"write", "payment", "pii", "irreversible"}
NO_CONFIRM = {"transfer_to_human", "send_sms_summary", "book_callback", "raise_complaint"}  # low-stakes writes

PROMISE = [r"pakka mil", r"zaroor mil", r"guarantee", r"definitely (get|be approved)", r"approve ho jayega",
           r"100 ?%", r"पक्का मिल", r"ज़रूर मिल", r"will (surely|definitely) get"]
DISCLAIMER = " Yeh sirf andaza hai; final approval documents aur credit check par depend karta hai."


def _canon(arguments):
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except json.JSONDecodeError:
        return None
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def charge_amount(name, a):
    """The exact rupees this action will move, computed by code (never by the model)."""
    try:
        if "amount" in a:
            return int(round(float(a["amount"])))
        if name == "pay_overdue":
            return (HANDLERS["get_overdue_emis"](loan_id=a["loan_id"])["total_overdue"]
                    + HANDLERS["get_late_fee"](loan_id=a["loan_id"])["late_fee"])
        if name.startswith("foreclose_") and name.endswith("_loan"):
            product = name[len("foreclose_"):-len("_loan")]
            return HANDLERS[f"get_{product}_loan_foreclosure_quote"](loan_id=a["loan_id"])["total_payable"]
        if name == "accept_settlement_offer":
            return HANDLERS["get_settlement_offer"](loan_id=a["loan_id"])["settlement_amount"]
    except (ToolError, KeyError, TypeError, ValueError):
        return None
    return None


def _readback(name, args, tool):
    """A short sentence for the agent to read back before a risky action."""
    a = json.loads(args)
    parts = [tool.description.rstrip(".")]
    amount = charge_amount(name, a)
    if amount is not None:
        parts.append(f"exact amount {amount:,} rupees")
    for key in ("loan_id", "card_id", "deposit_id", "application_id", "new_day", "months", "promise_date",
                "tenure_months", "instruction", "reason"):
        if key in a:
            parts.append(f"{key.replace('_', ' ')}: {a[key]}")
    return "; ".join(parts)


class SafetyLayer:
    def __init__(self, customer_id, executor_mode="local", audit=True, tools=None, run=None):
        """tools: name -> object with .risk, .needs, .description (default: the local catalog).
        run: function(name, arguments) -> outcome dict (default: the local backend executor),
        e.g. an MCP hub's call()."""
        self.customer_id = customer_id
        self.mode = executor_mode
        self.tools = tools if tools is not None else TOOLS_BY_NAME
        self.run = run or (lambda name, arguments: execute(name, arguments, mode=self.mode))
        self.pending = None          # (name, canonical_args) waiting for the caller's answer
        self.approved = None         # (name, canonical_args) the caller said yes to, usable once
        self.called_ok = set()       # tools that ran successfully in this call
        self.audit = audit
        self.events = []
        self.last_agent_message = ""   # set by the agent loop after each reply
        self.yes_to_agent = None       # the agent question the caller just said yes to

    # ---------- called by the agent loop ----------

    def on_user_turn(self, text):
        """Before the model sees the caller's words: did they just answer a confirmation?"""
        self.yes_to_agent = None
        if not self.pending:
            if self.last_agent_message.rstrip().endswith("?") and classify(text) == "yes":
                self.yes_to_agent = self.last_agent_message
            return None
        answer = classify(text)
        if answer == "yes":
            self.approved, self.pending = self.pending, None
            self._log("confirmed", *self.approved)
        elif answer == "no":
            self._log("cancelled_by_caller", *self.pending)
            self.pending = None
        else:
            self._log("unclear_answer", *self.pending, extra={"heard": text})
        return answer

    def execute(self, name, arguments):
        tools = self.tools() if callable(self.tools) else self.tools
        tool = tools.get(name)
        args = _canon(arguments)
        if tool is None or args is None:
            return self.run(name, arguments)  # the runner reports unknown tool / bad JSON

        if tool.risk in GATED and name not in NO_CONFIRM:
            if tool.risk == "irreversible" and tool.needs and not (set(tool.needs) & self.called_ok):
                self._log("blocked_missing_prerequisite", name, args)
                return {"ok": False, "error": "prerequisite_missing", "latency_ms": 0,
                        "message": f"First use {' or '.join(tool.needs)} and tell the caller the result. "
                                   f"Only then ask whether to go ahead."}
            if self.approved != (name, args) and self._agent_question_counts(tool, name, args):
                self._log("confirmed_via_agent_question", name, args)
                self.approved = (name, args)
            if self.approved != (name, args):
                self.pending, self.approved = (name, args), None
                self._log("confirmation_requested", name, args)
                return {"ok": False, "error": "confirmation_required", "latency_ms": 0,
                        "message": "Not done yet. Read this back to the caller and ask them to confirm with a "
                                   "clear yes: " + _readback(name, args, tool),
                        "read_back": _readback(name, args, tool)}
            self.approved = None  # one confirmation = one action

        outcome = self.run(name, arguments)
        if outcome["ok"]:
            self.called_ok.add(name)
            outcome["result"] = mask_for_model(outcome["result"])
        if tool.risk in GATED:
            self._log("executed" if outcome["ok"] else "failed", name, args,
                      extra={"error": outcome.get("error")})
        return outcome

    def _agent_question_counts(self, tool, name, args):
        if not self.yes_to_agent or tool.risk == "irreversible":
            return False
        if tool.risk == "payment":
            amount = charge_amount(name, json.loads(args))
            digits = re.sub(r"[^0-9]", "", self.yes_to_agent)
            return amount is not None and str(amount) in digits
        return True

    def check_reply(self, reply):
        """Last line of defence on what the agent says: never promise a loan."""
        if any(re.search(p, reply, re.I) for p in PROMISE):
            self._log("promise_softened", "reply", json.dumps({"reply": reply}, ensure_ascii=False))
            return reply.rstrip() + DISCLAIMER
        return reply

    # ---------- audit ----------

    def _log(self, event, name, args, extra=None):
        record = {"time": datetime.now().isoformat(timespec="seconds"), "customer_id": self.customer_id,
                  "event": event, "tool": name, "args": mask_for_log(json.loads(args)) if args else None,
                  **(mask_for_log(extra) if extra else {})}
        self.events.append(record)
        if self.audit:
            AUDIT.parent.mkdir(parents=True, exist_ok=True)
            with open(AUDIT, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
