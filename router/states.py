"""Hand-designed states: how a developer scopes tools on Sarvam's platform today (the baseline).

Sarvam's multi-state agents give each state its own tools, some global tools available
everywhere, and transitions between states when conditions are met. We model two designs a
developer might reasonably build for our lending agent:

    COARSE  6 big states (5 topic states + support)
    FINE    11 small states

and two ways of deciding the state on each turn:

    rules    keyword rules a developer writes ("card" -> cards). The state persists until a
             rule fires, as in a real conversation flow.
    oracle   always the right state (best case states could ever reach)

On the real platform the model also sees a transition option for every reachable state and
must call it to switch, which costs an extra model call. We count those switches.
"""
import re

from tools import TOOLS, TOOLS_BY_NAME
from router.index import tokens_for

GLOBAL = ["list_loans", "list_customer_products", "transfer_to_human", "faq_search", "raise_complaint",
          "book_callback", "verify_customer", "get_customer_profile"]

TRANSITION_TOKENS = 45  # one "go to <state>" function definition: name + one-line description


def _area(*areas, extra=(), drop=()):
    names = [t.name for t in TOOLS if t.area in areas and t.name not in drop]
    return [n for n in names if n not in GLOBAL] + [n for n in extra if n not in names]


COARSE = {
    "loan_servicing": _area("loans", "personal_loan", "home_loan", "vehicle_loan",
                            extra=["get_late_fee", "get_overdue_emis"]),
    "payments_collections": _area("payments", "collections", extra=["get_next_emi", "get_overdue_emis",
                                                                     "get_outstanding_amount"]),
    "new_loans_profile": _area("new_loans", "kyc_profile", "identity"),
    "cards": _area("cards"),
    "deposits": _area("deposits"),
}
COARSE["support"] = _area("support")  # anything left over (complaints, branches, callbacks...)

FINE = {
    "loan_info": _area("loans", "identity"),
    "personal_loan": _area("personal_loan", extra=["get_next_emi", "get_outstanding_amount"]),
    "home_loan": _area("home_loan", extra=["get_next_emi", "get_outstanding_amount", "get_interest_rate"]),
    "vehicle_loan": _area("vehicle_loan", extra=["get_next_emi", "get_outstanding_amount"]),
    "payments": _area("payments", extra=["get_next_emi", "get_overdue_emis"]),
    "collections": _area("collections", extra=["get_overdue_emis", "get_outstanding_amount", "get_late_fee"]),
    "new_loans": _area("new_loans", extra=["get_document_list"]),
    "profile_kyc": _area("kyc_profile", drop=["get_document_list"]),
    "cards": _area("cards"),
    "deposits": _area("deposits"),
    "support": _area("support"),
}

DESIGNS = {"coarse": COARSE, "fine": FINE}
DEFAULT_STATE = {"coarse": "loan_servicing", "fine": "loan_info"}

# Keyword rules, most specific first. Romanized Hinglish, Devanagari and English.
RULES = {
    "fine": [
        ("cards", r"card|कार्ड|transaction|reward|pin\b|limit|kharcha|खर्चा"),
        ("deposits", r"\bfd\b|एफडी|fixed deposit|deposit|matur|मैच्योर"),
        ("home_loan", r"home loan|होम लोन|ghar ka loan|flat|builder|बिल्डर|property|disburse|release|slab|itr|tax"),
        ("vehicle_loan", r"car|bike|gaadi|गाड़ी|vehicle|\brc\b|hypothecation|insurance|policy"),
        ("collections", r"settle|सेटल|holiday|restructur|naukri|नौकरी|job|promise|tareekh tak|तारीख तक|by the|"
                        r"reminder|call karna|call mat|recovery|overdue|nahi de sakta|नहीं दे सकता|can't pay"),
        ("payments", r"\bpay|bhar|भर|payment|upi|auto.?debit|autopay|refund|late fee|bounce|link|minimum|clear"),
        ("new_loans", r"eligib|naya loan|new loan|apply|application|आवेदन|interest rate|ब्याज दर|rate|calculat|"
                      r"documents?|offer|milega|मिलेगा|can i get"),
        ("profile_kyc", r"kyc|केवाईसी|address|पता|ghar badal|घर बदल|moved|phone number|mobile number|email|"
                        r"nominee|नॉमिनी|\bpan\b|language|bhasha"),
        ("personal_loan", r"personal|पर्सनल|top.?up|part payment|bonus|extra"),
        ("support", r"branch|शाखा|complaint|शिकायत|rude|बदतमीज़ी|insaan|इंसान|person|human|callback"),
        ("loan_info", r"emi|ईएमआई|loan|लोन|noc|एनओसी|statement|baaki|बाकी|outstanding|owe"),
    ],
}
# Coarse rules reuse the fine ones, mapped onto the bigger states
_COARSE_OF = {"loan_info": "loan_servicing", "personal_loan": "loan_servicing", "home_loan": "loan_servicing",
              "vehicle_loan": "loan_servicing", "payments": "payments_collections",
              "collections": "payments_collections", "new_loans": "new_loans_profile",
              "profile_kyc": "new_loans_profile", "cards": "cards", "deposits": "deposits", "support": "support"}
RULES["coarse"] = [(_COARSE_OF[s], p) for s, p in RULES["fine"]]


def state_of_tool(design, name):
    return [s for s, tools in DESIGNS[design].items() if name in tools]


def check():
    """Every tool must be reachable: in a state or global."""
    for design, states in DESIGNS.items():
        covered = set(GLOBAL).union(*states.values())
        missing = [t.name for t in TOOLS if t.name not in covered]
        assert not missing, f"{design}: tools in no state: {missing}"
        unknown = [n for tools in states.values() for n in tools if n not in TOOLS_BY_NAME]
        assert not unknown, f"{design}: unknown tools {unknown}"


class StateRouter:
    def __init__(self, design, mode="rules"):
        check()
        self.design, self.mode = design, mode
        self.states = DESIGNS[design]
        self.rules = [(s, re.compile(p, re.I)) for s, p in RULES[design]]
        self.current = None

    def reset(self):
        self.current = None

    def detect(self, text):
        for state, pattern in self.rules:
            if pattern.search(text):
                return state
        return None

    def oracle(self, all_of):
        """The state covering the most needed tool groups (ties: the first group's state)."""
        best, best_cover = None, -1
        for s, tools in self.states.items():
            pool = set(tools) | set(GLOBAL)
            cover = sum(bool(pool & set(g)) for g in all_of)
            if cover > best_cover:
                best, best_cover = s, cover
        return best

    def select(self, text, all_of=None):
        """Returns (tools exposed, switched_state)."""
        previous = self.current
        if self.mode == "oracle":
            self.current = self.oracle(all_of)
        else:
            # keyword rule if one fires, else stay in the current state, else start in the main loan state
            self.current = self.detect(text) or self.current or DEFAULT_STATE[self.design]
        tools = list(dict.fromkeys(self.states[self.current] + GLOBAL))
        return tools, previous is not None and previous != self.current

    def tokens(self, tools):
        """Tool definitions + one transition function per other state."""
        return tokens_for(tools) + TRANSITION_TOKENS * (len(self.states) - 1)


def summary():
    check()
    for design, states in DESIGNS.items():
        sizes = {s: len(t) for s, t in states.items()}
        print(f"{design}: {len(states)} states, sizes {sizes}, + {len(GLOBAL)} global tools, "
              f"{sum(sizes.values())} tool-to-state assignments, {len(RULES[design])} keyword rules")


if __name__ == "__main__":
    summary()


# ---------- live states: the LLM switches state itself, like Sarvam's platform ----------

STATE_DESCRIPTIONS = {
    "coarse": {
        "loan_servicing": "loan details, EMIs and schedules, statements, NOC, foreclosure, home, vehicle and "
                          "personal loan servicing",
        "payments_collections": "paying EMIs, payment problems, autopay, refunds, late fees, overdue EMIs, "
                                "settlement, EMI holiday, reminder calls",
        "new_loans_profile": "new loans, eligibility, interest rates, applications, KYC, address, phone, email, "
                             "nominee or PAN changes",
        "cards": "credit card bill and payments, limits, transactions, disputes, rewards, blocking a card",
        "deposits": "fixed deposits: rates, opening, maturity, breaking, certificates",
        "support": "complaints, branches, callbacks",
    },
}


class LiveStates:
    """State machine for the live agent: per-state tools + globals + one go_to_<state> function per
    other state. When the model calls go_to_X, the agent switches and gets X's tools."""

    def __init__(self, design="coarse", start=None):
        self.design = design
        self.states = DESIGNS[design]
        self.current = start or DEFAULT_STATE[design]
        self.transitions = 0

    def select_tools(self, messages):
        return list(dict.fromkeys(self.states[self.current] + GLOBAL))

    def transition_schemas(self):
        return [{"type": "function", "function": {
            "name": f"go_to_{s}",
            "description": f"Switch the conversation to the '{s}' topic: {STATE_DESCRIPTIONS[self.design][s]}. "
                           f"Use when the caller needs something from that topic; you then get its tools.",
            "parameters": {"type": "object", "properties": {}, "required": []}}}
            for s in self.states if s != self.current]

    def handles(self, name):
        return name.startswith("go_to_") and name[len("go_to_"):] in self.states

    def switch(self, name):
        self.current = name[len("go_to_"):]
        self.transitions += 1
        return {"now_in": self.current, "note": "You now have this topic's tools. Continue with the caller's request."}
