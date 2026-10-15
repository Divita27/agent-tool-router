"""Check the safety layer enforces its rules, with no LLM involved.

Run from the project folder:  python -m scripts.test_safety
Each test plays the model's part (asking for tools) and the caller's part (saying yes / no).
"""
import contextlib
import io
import json

from backend import seed
from backend.db import db
from safety.confirm import classify
from safety.layer import SafetyLayer
from safety.pii import mask_for_log, mask_text

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def fresh():
    with contextlib.redirect_stdout(io.StringIO()):
        seed.main()
    return SafetyLayer("C001", audit=False)


def card_status():
    with db() as conn:
        return conn.execute("SELECT status FROM credit_cards WHERE id='CC2001'").fetchone()[0]


# 1. An irreversible action never runs without a yes
s = fresh()
out = s.execute("block_card", '{"card_id": "CC2001", "reason": "lost"}')
check("block_card without yes is held", out["error"] == "confirmation_required" and card_status() == "active")
check("read-back explains the action", "block" in out["read_back"].lower(), out.get("read_back"))

# 2. After a clear yes, the SAME call runs once
check("'haan kar do' is a yes", s.on_user_turn("haan kar do") == "yes")
out = s.execute("block_card", '{"reason": "lost", "card_id": "CC2001"}')  # same args, different order
check("same call after yes runs", out["ok"] and card_status() == "blocked")
out = s.execute("block_card", '{"card_id": "CC2001", "reason": "lost"}')
check("a yes covers one action only", out["error"] == "confirmation_required")

# 3. A no cancels
s = fresh()
s.execute("pay_emi", '{"loan_id": "L1001", "amount": 13538}')
check("'nahi ruko' is a no", s.on_user_turn("nahi ruko") == "no")
out = s.execute("pay_emi", '{"loan_id": "L1001", "amount": 13538}')
check("after no, payment is asked again (not run)", out["error"] == "confirmation_required")

# 4. Changing the details after a yes needs a new yes
s = fresh()
s.execute("pay_emi", '{"loan_id": "L1001", "amount": 13538}')
s.on_user_turn("haan")
out = s.execute("pay_emi", '{"loan_id": "L1001", "amount": 27076}')
check("changed amount after yes is held", out["error"] == "confirmation_required")

# 5. Mixed answers never count as yes
s = fresh()
s.execute("cancel_autopay", '{"loan_id": "L1005"}')
check("'haan... nahi ruko' is unclear", s.on_user_turn("haan... nahi ruko") == "unclear")
out = s.execute("cancel_autopay", '{"loan_id": "L1005"}')
check("unclear answer does not approve", out["error"] == "confirmation_required")

# 6. Irreversible actions need their prerequisite in this call
s = fresh()
out = s.execute("foreclose_personal_loan", '{"loan_id": "L1001"}')
check("foreclose without quote is blocked", out["error"] == "prerequisite_missing", out.get("message"))
check("quote (read) runs without asking", s.execute("get_personal_loan_foreclosure_quote", '{"loan_id": "L1001"}')["ok"])
out = s.execute("foreclose_personal_loan", '{"loan_id": "L1001"}')
check("after quote, foreclose asks for a yes", out["error"] == "confirmation_required")
s.on_user_turn("ji theek hai, band kar do")
check("after quote + yes, foreclose runs", s.execute("foreclose_personal_loan", '{"loan_id": "L1001"}')["ok"])

# 7. Read tools and low-stakes actions run straight away
s = fresh()
check("read tool runs immediately", s.execute("get_next_emi", '{"loan_id": "L1001"}')["ok"])
check("transfer_to_human runs immediately", s.execute("transfer_to_human", '{"reason": "asked"}')["ok"])

# 8. PII is masked before the model sees it
out = s.execute("get_registered_contact_details", '{"customer_id": "C001"}')
check("PII tool needs a yes too", out["error"] == "confirmation_required")
s.on_user_turn("yes")
out = s.execute("get_registered_contact_details", '{"customer_id": "C001"}')
r = out["result"]
check("phone masked for the model", r["phone"] == "XXXXXX0001", r["phone"])
check("email masked for the model", "•••" in r["email"] and "ravi.kumar" not in r["email"], r["email"])
check("address kept for the model (needed to confirm changes)", "MG Road" in r["address"])

# 9. Logs never hold raw identifiers or addresses
log = mask_for_log({"account_number": "123456789012", "new_address": "12, Indiranagar, Bengaluru",
                    "note": "call me on 9876500001, PAN ABCPK1234F, a/c 123456789012"})
flat = json.dumps(log)
check("logs mask account numbers", "123456789012" not in flat and "9012" in flat, flat)
check("logs mask phone + PAN in free text", "9876500001" not in flat and "ABCPK1234F" not in flat, flat)
check("logs drop addresses", "Indiranagar" not in flat)
masked = mask_text("my PAN is ABCPK1234F, phone +91 9876500001")
check("free-text masking", "ABCPK1234F" not in masked and "9876500001" not in masked, masked)

# 10. No loan promises in what the agent says
s = fresh()
reply = s.check_reply("Haan, aapko 5 lakh ka loan pakka mil jayega!")
check("promise gets a disclaimer", "andaza" in reply, reply)
check("normal reply unchanged", s.check_reply("Aapki EMI 5 November ko hai.") == "Aapki EMI 5 November ko hai.")

# 11. The audit trail records the whole story
s = fresh()
s.execute("block_card", '{"card_id": "CC2001", "reason": "stolen"}')
s.on_user_turn("haan")
s.execute("block_card", '{"card_id": "CC2001", "reason": "stolen"}')
check("audit: requested -> confirmed -> executed",
      [e["event"] for e in s.events] == ["confirmation_requested", "confirmed", "executed"],
      [e["event"] for e in s.events])

# 12. Yes/no edge cases
for text, want in [("yahaan pe payment kiya tha", "unclear"), ("यहाँ से", "unclear"), ("हाँ कर दीजिए", "yes"),
                   ("kar do na", "yes"), ("theek hai lekin abhi nahi", "unclear"), ("No, wait", "no"),
                   ("rehne do", "no"), ("ok go ahead", "yes")]:
    check(f"classify {text!r} = {want}", classify(text) == want, classify(text))

# 13. A yes to the agent's own question: counts for writes, for payments only with the exact amount
s = fresh()
s.last_agent_message = "EMI date 10 tareekh kar doon?"
s.on_user_turn("haan")
check("write: yes to agent's question counts", s.execute("change_emi_date", '{"loan_id": "L1003", "new_day": 10}')["ok"])
s.last_agent_message = "Overdue 27,076 rupees UPI se pay kar doon?"
s.on_user_turn("haan kar do")
out = s.execute("pay_overdue", '{"loan_id": "L1001"}')
check("payment: agent said wrong total (late fee missing) -> system read-back", out["error"] == "confirmation_required")
check("read-back states the true total incl. late fee", "28,076" in out["read_back"], out["read_back"])
s.pending = None
s.last_agent_message = "Overdue EMIs aur late fee milake 28,076 rupees pay kar doon?"
s.on_user_turn("haan")
check("payment: agent stated exact amount -> one yes is enough", s.execute("pay_overdue", '{"loan_id": "L1001"}')["ok"])
s = fresh()
s.execute("get_card_transactions", '{"card_id": "CC2001"}')
s.last_agent_message = "Card ending 0520 block kar doon?"
s.on_user_turn("haan")
out = s.execute("block_card", '{"card_id": "CC2001", "reason": "lost"}')
check("irreversible: always the system's read-back", out["error"] == "confirmation_required")
s = fresh()
s.last_agent_message = "Aapki EMI 5 November ko hai."  # not a question
s.on_user_turn("ok")
out = s.execute("change_emi_date", '{"loan_id": "L1003", "new_day": 10}')
check("'ok' to a statement is not a confirmation", out["error"] == "confirmation_required")

with contextlib.redirect_stdout(io.StringIO()):
    seed.main()  # leave a clean database
passed = sum(ok for _, ok, _ in results)
print(f"{passed}/{len(results)} safety checks passed")
for name, ok, detail in results:
    if not ok:
        print(f"  FAIL {name}  {detail}")
