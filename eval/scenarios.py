"""Test scenarios: what recorders are asked to do, and what the right answer is.

Each scenario is a card handed to a recorder. The card describes a SITUATION, never the
words to say, so people speak naturally. Labels say what a good agent should do.

Labels per turn:
    tools    tools that would correctly serve this turn. The router must keep at least one
             of them (recall). If outcome is "call", the agent should call one of them.
    outcome  "call"     the agent should call one of `tools` this turn
             "clarify"  the agent should ask a question first (which loan? which account?)
             "either"   calling or asking first are both fine
    args     key arguments the main tool call should have (checked in Step 11)

Conversations: recorders can't talk to a live agent, so each turn (except the last) has a
fixed `agent_reply` printed on their card. They read it, then record their next turn. The
benchmark uses the same replies as the conversation history, so every recorded turn is tested
in exactly the context the speaker was responding to.

`dev_text` is ONE phrasing written by Claude, for developing the router before recordings
exist. It is NOT test data and must never be reported as a result.

"Today" in the fake company is 13 October 2026.
"""
from dataclasses import dataclass, field

TODAY_NOTE = "Today is 13 October 2026."

PERSONAS = {
    "C001": ("Ravi Kumar", "You are Ravi, from Bengaluru. You have two loans with Demo Lending: a personal loan "
             "(EMI 13,538 rupees, due on the 5th; you missed the September and October EMIs) and a home loan for a "
             "flat that is still being built. You also have their credit card."),
    "C002": ("Priya Sharma", "You are Priya, from Mumbai. You have a personal loan you pay on time, an older "
             "personal loan you fully paid off last year, and a car loan. You also have a fixed deposit of "
             "2 lakh rupees with them."),
    "C003": ("Arjun Reddy", "You are Arjun, from Hyderabad. You lost your job a few months ago and have missed "
             "4 EMIs on your personal loan. You had a bike loan that you finished paying. Last week you applied "
             "for a new small loan (application number A4001). Your KYC is not complete."),
}


@dataclass
class Turn:
    situation: str          # shown on the card
    tools: list
    outcome: str = "call"
    args: dict = field(default_factory=dict)
    dev_text: str = ""      # Claude's phrasing, dev only
    tags: list = field(default_factory=list)
    agent_reply: str = ""   # multi-turn: the agent's reply shown on the card before the next turn


@dataclass
class Scenario:
    id: str
    customer: str
    turns: list
    tags: list = field(default_factory=list)

    @property
    def multi_turn(self):
        return len(self.turns) > 1


S = Scenario
T = Turn

SCENARIOS = [
    # =================================================================
    # Single-turn: Ravi (C001)
    # =================================================================
    S("S01", "C001", [T("Ask when your next personal loan EMI is due and how much it is.",
                        ["get_next_emi"], args={"loan_id": "L1001"},
                        dev_text="Personal loan ki agli EMI kab hai aur kitni hai?")]),
    S("S02", "C001", [T("Ask when your next EMI is due. Don't say which loan.",
                        ["get_next_emi"], outcome="clarify",
                        dev_text="Meri agli EMI kab due hai?")], tags=["ambiguous"]),
    S("S03", "C001", [T("Ask how many EMIs you have missed on your personal loan.",
                        ["get_overdue_emis", "get_outstanding_amount"], args={"loan_id": "L1001"},
                        dev_text="Personal loan ki kitni EMI miss ho gayi hain?")]),
    S("S04", "C001", [T("You paid your personal loan EMI by UPI two days ago, but it still shows as unpaid. "
                        "Complain about it.",
                        ["report_payment_not_reflected", "get_payment_history", "get_last_payment"],
                        args={"loan_id": "L1001"},
                        dev_text="Maine parso UPI se EMI bhari thi, abhi tak dikh nahi rahi")]),
    S("S05", "C001", [T("Ask how much of your home loan has been paid to the builder so far.",
                        ["get_home_loan_disbursement_status"], args={"loan_id": "L1002"},
                        dev_text="Home loan ka kitna paisa builder ko mil chuka hai?")]),
    S("S06", "C001", [T("The builder finished the slab and needs the next payment. Ask for 5 lakh rupees to be "
                        "released from your home loan.",
                        ["request_home_loan_disbursement"],
                        args={"loan_id": "L1002", "amount": 500000},
                        dev_text="Slab ho gaya hai, builder ko 5 lakh release kar do home loan se")]),
    S("S07", "C001", [T("Ask how much it would cost to close your home loan completely right now.",
                        ["get_home_loan_foreclosure_quote"], args={"loan_id": "L1002"},
                        dev_text="Home loan abhi poora band karna ho to kitna dena padega?")], tags=["lookalike"]),
    S("S08", "C001", [T("Ask how much your credit card bill is and when it is due.",
                        ["get_card_bill"], args={"card_id": "CC2001"},
                        dev_text="Credit card ka bill kitna hai aur kab tak bharna hai?")]),
    S("S09", "C001", [T("You lost your credit card yesterday. Get it stopped so nobody can use it.",
                        ["block_card"], args={"card_id": "CC2001", "reason": "lost"},
                        dev_text="Mera credit card kal kho gaya, usko band kar do")], tags=["irreversible"]),
    S("S10", "C001", [T("There is a charge on your credit card that you didn't make. Report it.",
                        ["get_card_transactions", "dispute_card_transaction"], outcome="either",
                        dev_text="Card pe ek kharcha dikh raha hai jo maine kiya hi nahi")]),
    S("S11", "C001", [T("Ask how much late fee has been charged on your personal loan.",
                        ["get_late_fee"], args={"loan_id": "L1001"},
                        dev_text="Personal loan pe kitni late fee lagi hai?")]),
    S("S12", "C001", [T("You were in hospital and that's why you missed EMIs. Ask them to remove the late fee "
                        "on your personal loan.",
                        ["request_late_fee_waiver"], args={"loan_id": "L1001"},
                        dev_text="Main hospital mein tha isliye EMI miss hui, late fee maaf kar do please")]),
    S("S13", "C001", [T("You need the home loan certificate for your income tax return for last financial year.",
                        ["get_home_loan_tax_certificate", "get_interest_certificate"],
                        args={"loan_id": "L1002", "financial_year": "2025-26"},
                        dev_text="ITR ke liye pichhle saal ka home loan certificate chahiye")], tags=["lookalike"]),
    S("S14", "C001", [T("Say you want to clear all the pending EMIs on your personal loan right now.",
                        ["pay_overdue", "send_payment_link"], args={"loan_id": "L1001"},
                        dev_text="Personal loan ki saari pending EMI abhi clear karni hai")], tags=["payment"]),
    S("S15", "C001", [T("You can't pay the missed personal loan EMIs today. Say you'll pay on the 20th of this month.",
                        ["record_promise_to_pay"], outcome="either",
                        args={"loan_id": "L1001", "promise_date": "2026-10-20"},
                        dev_text="Abhi paise nahi hain, 20 tareekh tak pakka bhar dunga")]),
    S("S16", "C001", [T("You're frustrated with the automated agent. Ask to speak to a real person.",
                        ["transfer_to_human"],
                        dev_text="Mujhe kisi insaan se baat karni hai, robot se nahi")]),
    S("S17", "C001", [T("You changed banks. Say you want your EMIs to be auto-debited from your new bank account. "
                        "Don't give the account number yet.",
                        ["update_autopay_bank_account"], outcome="clarify",
                        dev_text="Mera bank account badal gaya hai, EMI ab naye account se katni chahiye")]),

    # =================================================================
    # Single-turn: Priya (C002)
    # =================================================================
    S("S18", "C002", [T("Your old personal loan is fully paid off. Ask for the NOC (no objection certificate).",
                        ["request_noc", "get_noc_status"], args={"loan_id": "L1004"},
                        dev_text="Mera purana personal loan band ho gaya hai, uska NOC chahiye")],
      tags=["two_personal_loans"]),
    S("S19", "C002", [T("You renewed your car insurance. Give them the new policy number VI778899, valid till "
                        "30 September 2027.",
                        ["update_vehicle_insurance"],
                        args={"loan_id": "L1005", "policy_number": "VI778899", "expiry_date": "2027-09-30"},
                        dev_text="Car insurance renew kar liya hai, naya policy number VI778899 hai, "
                                 "30 September 2027 tak valid")], tags=["args"]),
    S("S20", "C002", [T("Ask how much you would need to pay to close your car loan early.",
                        ["get_vehicle_loan_foreclosure_quote"], args={"loan_id": "L1005"},
                        dev_text="Car loan jaldi band karna hai, kitna lagega?")], tags=["lookalike"]),
    S("S21", "C002", [T("Ask how much it would cost to close your loan. Don't say which loan.",
                        ["get_personal_loan_foreclosure_quote", "get_vehicle_loan_foreclosure_quote"],
                        outcome="clarify",
                        dev_text="Loan band karne mein kitna paisa lagega?")], tags=["ambiguous", "lookalike"]),
    S("S22", "C002", [T("Ask when your fixed deposit matures.",
                        ["get_deposit_details", "list_deposits"],
                        dev_text="Meri FD kab mature hogi?")]),
    S("S23", "C002", [T("When your fixed deposit matures, you want the money paid out, not renewed. Tell them.",
                        ["update_fd_maturity_instruction"],
                        args={"deposit_id": "FD3001", "instruction": "payout"},
                        dev_text="FD mature ho to renew mat karna, paisa account mein daal dena")]),
    S("S24", "C002", [T("Add your father, Anil Sharma, as the nominee on your account.",
                        ["update_nominee"], args={"nominee_name": "Anil Sharma", "relation": "father"},
                        dev_text="Nominee mein mere papa Anil Sharma ka naam daal do")], tags=["args"]),
    S("S25", "C002", [T("You got a bonus. Ask what happens if you pay 50,000 rupees extra towards your "
                        "personal loan.",
                        ["get_personal_loan_part_payment_quote"], args={"loan_id": "L1003", "amount": 50000},
                        dev_text="Bonus mila hai, personal loan mein 50 hazaar extra daalun to kya hoga?")]),
    S("S26", "C002", [T("Ask if you can get 1 lakh rupees more on your current personal loan.",
                        ["request_personal_loan_top_up", "get_preapproved_offers"],
                        args={"loan_id": "L1003", "amount": 100000},
                        dev_text="Mere chalu personal loan pe 1 lakh aur mil sakta hai kya?")]),
    S("S27", "C002", [T("You earn 1.4 lakh a month and want to buy a flat worth 80 lakh. Ask how much home loan "
                        "you can get.",
                        ["check_home_loan_eligibility"],
                        args={"monthly_income": 140000, "property_value": 8000000},
                        dev_text="Meri salary 1.4 lakh hai, 80 lakh ka flat lena hai, kitna home loan milega?")],
      tags=["no_promise", "args"]),
    S("S28", "C002", [T("Ask to change the EMI date of your personal loan to the 10th.",
                        ["change_emi_date"], args={"loan_id": "L1003", "new_day": 10},
                        dev_text="Personal loan ki EMI date 10 tareekh kar do")]),
    S("S29", "C002", [T("You're thinking of buying a second car. Ask what interest rate they charge on car "
                        "loans these days.",
                        ["get_current_interest_rates"], args={"product": "vehicle"},
                        dev_text="Aajkal car loan pe kitna interest chal raha hai?")], tags=["lookalike"]),
    S("S30", "C002", [T("Ask what the EMI would be on a 5 lakh loan for 3 years at 12% interest.",
                        ["calculate_emi"], args={"principal": 500000, "annual_rate": 12, "tenure_months": 36},
                        dev_text="5 lakh ka loan 3 saal ke liye 12 percent pe, EMI kitni banegi?")], tags=["args"]),

    # =================================================================
    # Single-turn: Arjun (C003)
    # =================================================================
    S("S31", "C003", [T("Explain that you lost your job and can't pay EMIs right now. Ask what can be done.",
                        ["report_financial_hardship", "request_emi_holiday", "request_loan_restructuring",
                         "get_settlement_offer"], outcome="either",
                        dev_text="Meri naukri chali gayi hai, abhi EMI nahi bhar sakta, kya kar sakte hain?")]),
    S("S32", "C003", [T("Ask if you can settle your personal loan with a one-time payment.",
                        ["get_settlement_offer"], args={"loan_id": "L1007"},
                        dev_text="Personal loan ka ek baar mein settlement ho sakta hai kya?")]),
    S("S33", "C003", [T("Your bike loan is finished. You want the bank's name removed from your bike's RC.",
                        ["request_hypothecation_removal", "get_hypothecation_status"], args={"loan_id": "L1006"},
                        dev_text="Bike loan khatam ho gaya, RC se bank ka naam hatwana hai")]),
    S("S34", "C003", [T("You got a message that your KYC is incomplete. Ask how to complete it.",
                        ["start_video_kyc", "get_kyc_status"],
                        dev_text="KYC incomplete ka message aaya hai, kaise complete karun?")]),
    S("S35", "C003", [T("Ask what's happening with your loan application. Say the application number A4001.",
                        ["get_application_status"], args={"application_id": "A4001"},
                        dev_text="Mera loan application A4001 kahan tak pahuncha?")], tags=["args"]),
    S("S36", "C003", [T("For your loan application A4001, ask how to send the documents.",
                        ["send_document_upload_link", "get_document_list"], args={"application_id": "A4001"},
                        dev_text="Application A4001 ke documents kaise bhejun?")]),
    S("S37", "C003", [T("You keep getting reminder calls in the morning. Ask them to call you only between "
                        "5 and 7 in the evening.",
                        ["update_collection_call_preferences"], args={"preferred_time": "17:00-19:00"},
                        dev_text="Mujhe subah call mat karo, sirf shaam 5 se 7 ke beech karna")]),
    S("S38", "C003", [T("You work late. Ask them to make reminder calls only between 8 and 10 at night.",
                        ["update_collection_call_preferences", "get_collection_call_preferences"],
                        outcome="either",
                        dev_text="Main late tak kaam karta hoon, reminder call raat 8 se 10 ke beech karna")],
      tags=["rules"]),
    S("S39", "C003", [T("Ask where the nearest branch is. Your pincode is 500034.",
                        ["find_nearest_branch"], args={"pincode": "500034"},
                        dev_text="500034 pincode ke paas branch kahan hai?")]),
    S("S40", "C003", [T("A recovery agent was rude to you on the phone. Complain about it.",
                        ["raise_complaint"], args={"category": "rude_agent"},
                        dev_text="Aapke recovery agent ne mujhse bahut badtameezi se baat ki")]),
    S("S41", "C003", [T("Ask how much in total you still owe on your personal loan.",
                        ["get_outstanding_amount"], args={"loan_id": "L1007"},
                        dev_text="Personal loan pe total kitna baaki hai?")]),
    S("S42", "C003", [T("Ask if you will definitely get a new 5 lakh personal loan. Push for a yes.",
                        ["check_personal_loan_eligibility", "get_preapproved_offers"], outcome="either",
                        dev_text="5 lakh ka personal loan pakka milega na? Haan bolo")], tags=["no_promise"]),

    # =================================================================
    # Multi-turn conversations (record each turn as a separate clip)
    # =================================================================
    S("M01", "C001", [
        T("Ask when your next EMI is. Don't say which loan.", ["get_next_emi"], outcome="clarify",
          dev_text="Agli EMI kab hai?",
          agent_reply="Aapke do loan hain, personal loan aur home loan. Aap kis loan ki EMI ke baare mein "
                      "poochh rahe hain?"),
        T("Answer: the personal loan.", ["get_next_emi"], args={"loan_id": "L1001"},
          dev_text="Personal wala", tags=["reference"],
          agent_reply="Aapke personal loan ki agli EMI 5 November ko 13,538 rupees hai. Pichhli do EMIs bhi "
                      "abhi pending hain."),
        T("Say you want to pay it right now.", ["pay_emi", "pay_overdue", "send_payment_link"],
          args={"loan_id": "L1001"}, dev_text="Theek hai, abhi bhar deta hoon", tags=["reference", "payment"]),
    ]),
    S("M02", "C001", [
        T("Ask how much your credit card bill is.", ["get_card_bill"], args={"card_id": "CC2001"},
          dev_text="Card ka bill kitna aaya hai?",
          agent_reply="Aapka credit card bill 42,300 rupees hai. Minimum due 2,115 rupees hai, aur last date "
                      "20 October hai."),
        T("Say you'll pay only the minimum amount now.", ["pay_card_bill"],
          args={"card_id": "CC2001", "amount": 2115}, dev_text="Abhi sirf minimum bhar deta hoon",
          tags=["reference", "payment"]),
    ]),
    S("M03", "C001", [
        T("Ask how much it would cost to close your home loan now.", ["get_home_loan_foreclosure_quote"],
          args={"loan_id": "L1002"}, dev_text="Home loan band karne mein kitna lagega?",
          agent_reply="Home loan abhi band karne ke liye lagbhag 43 lakh 70 hazaar rupees lagenge. Home loan "
                      "par koi foreclosure charge nahi hai."),
        T("Now ask the same thing for your personal loan, in very few words.",
          ["get_personal_loan_foreclosure_quote"], args={"loan_id": "L1001"},
          dev_text="Aur personal wale ka?", tags=["reference", "lookalike"]),
    ]),
    S("M04", "C002", [
        T("Your old personal loan is closed. Ask if the NOC has been sent.", ["get_noc_status", "request_noc"],
          args={"loan_id": "L1004"}, dev_text="Purana loan band ho gaya tha, NOC bheja kya?",
          agent_reply="Aapke purane personal loan ke liye abhi tak NOC ki request nahi daali gayi hai."),
        T("Ask them to request it now.", ["request_noc"], args={"loan_id": "L1004"},
          dev_text="To abhi request daal do", tags=["reference"]),
    ]),
    S("M05", "C002", [
        T("Ask what interest rates they give on fixed deposits.", ["get_fd_rates"],
          dev_text="FD pe kitna interest dete ho?",
          agent_reply="1 saal ki FD par 7.1 percent, 2 saal par 7.25, 3 saal par 7.5, aur 5 saal par 7.75 "
                      "percent interest hai."),
        T("Say you want to open one for 1 lakh rupees for 2 years.", ["open_fixed_deposit"],
          args={"amount": 100000, "tenure_months": 24}, dev_text="1 lakh ki 2 saal wali bana do",
          tags=["reference", "payment"]),
    ]),
    S("M06", "C003", [
        T("Ask how many EMIs are overdue on your loan.", ["get_overdue_emis", "get_outstanding_amount"],
          args={"loan_id": "L1007"}, dev_text="Kitni EMI overdue hain?",
          agent_reply="Aapki 4 EMIs overdue hain, June se September tak. Total 35,156 rupees baaki hai."),
        T("Say you can't pay now but will pay by the 25th.", ["record_promise_to_pay"], outcome="either",
          args={"loan_id": "L1007", "promise_date": "2026-10-25"},
          dev_text="Abhi nahi de sakta, 25 tareekh tak de dunga", tags=["reference"]),
    ]),
    S("M07", "C003", [
        T("Ask if you can settle your loan.", ["get_settlement_offer"], args={"loan_id": "L1007"},
          dev_text="Settlement ho sakta hai kya?",
          agent_reply="Haan, aap 1 lakh 30 hazaar rupees ek baar mein dekar loan settle kar sakte hain. Lekin "
                      "credit report mein 'settled' dikhega, jisse aapka score kam ho sakta hai."),
        T("Say yes, you accept the offer.", ["accept_settlement_offer"],
          args={"loan_id": "L1007", "offer_id": "SO-L1007"}, dev_text="Haan theek hai, accept karta hoon",
          tags=["reference", "irreversible"]),
    ]),
    S("M08", "C001", [
        T("Ask to hear your recent credit card transactions.", ["get_card_transactions"],
          args={"card_id": "CC2001"}, dev_text="Card ke last transactions batao",
          agent_reply="Aapke last teen transactions: 6 October ko Electronics Hub par 26,050 rupees, 6 October "
                      "ko Mobile Recharge par 21,650 rupees, aur 5 October ko Rail Booking par 14,400 rupees."),
        T("Say the Electronics Hub one isn't yours.", ["dispute_card_transaction"],
          args={"card_id": "CC2001", "transaction_id": "T600001"},
          dev_text="Electronics Hub wala maine nahi kiya", tags=["reference"]),
    ]),
    S("M09", "C002", [
        T("Ask when your car insurance expires, according to their records.", ["get_vehicle_insurance_status"],
          args={"loan_id": "L1005"}, dev_text="Mere car insurance ki expiry kab ki hai aapke record mein?",
          agent_reply="Hamare record mein aapki car insurance 6 February 2027 tak valid hai."),
        T("Say you've already renewed it early: new policy number VI778899, valid till 30 September 2027.",
          ["update_vehicle_insurance"],
          args={"loan_id": "L1005", "policy_number": "VI778899", "expiry_date": "2027-09-30"},
          dev_text="Maine pehle hi renew kar liya, naya number VI778899, 30 September 2027 tak",
          tags=["reference", "args"]),
    ]),
    S("M10", "C003", [
        T("Ask about your loan application. The number is A4001.", ["get_application_status"],
          args={"application_id": "A4001"}, dev_text="Application number A4001 ka kya status hai?",
          agent_reply="Aapki application A4001 mein documents pending hain. Documents milte hi hum aage "
                      "process karenge."),
        T("Ask how to send them the documents.", ["send_document_upload_link", "get_document_list"],
          args={"application_id": "A4001"}, dev_text="Documents kaise bhejun?", tags=["reference"]),
    ]),
    S("M11", "C001", [
        T("Ask when your next personal loan EMI is.", ["get_next_emi"], args={"loan_id": "L1001"},
          dev_text="Personal loan ki agli EMI kab hai?",
          agent_reply="Aapke personal loan ki agli EMI 5 November ko 13,538 rupees hai."),
        T("Change topic: say you've moved house and want to update your address. Don't give the address yet.",
          ["update_address"], outcome="clarify", dev_text="Aur haan, maine ghar badal liya hai, address update karna hai",
          tags=["topic_switch"]),
    ]),
    S("M12", "C002", [
        T("Ask how much it would cost to close your car loan.", ["get_vehicle_loan_foreclosure_quote"],
          args={"loan_id": "L1005"}, dev_text="Car loan band karne mein kitna lagega?",
          agent_reply="Car loan abhi band karne ke liye lagbhag 7 lakh 27 hazaar rupees lagenge, jismein 3 "
                      "percent foreclosure charge shamil hai."),
        T("Say forget it, instead just move that loan's EMI date to the 10th.", ["change_emi_date"],
          args={"loan_id": "L1005", "new_day": 10}, dev_text="Rehne do, bas uski EMI date 10 kar do",
          tags=["reference", "topic_switch"]),
    ]),
]

SCENARIOS_BY_ID = {s.id: s for s in SCENARIOS}
