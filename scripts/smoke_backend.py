"""Call every backend tool once with realistic arguments and check the outcome.

Run from the project folder:  python -m scripts.smoke_backend
It rebuilds the database first, so it's safe to run any time.
"""
import contextlib
import io

from backend import seed
from backend.handlers import HANDLERS, ToolError

# (tool, args, expected): expected is "ok" or the ToolError code we expect
CASES = [
    # customer and identity
    ("find_customer_by_phone", {"phone": "+91 98765 00001"}, "ok"),
    ("find_customer_by_phone", {"phone": "9000000000"}, "not_found"),
    ("verify_customer", {"customer_id": "C001", "dob": "1988-04-12"}, "ok"),
    ("get_customer_profile", {"customer_id": "C001"}, "ok"),
    ("list_customer_products", {"customer_id": "C002"}, "ok"),
    # loans
    ("list_loans", {"customer_id": "C001"}, "ok"),
    ("get_loan_details", {"loan_id": "L1001"}, "ok"),
    ("get_outstanding_amount", {"loan_id": "L1001"}, "ok"),
    ("get_next_emi", {"loan_id": "L1001"}, "ok"),
    ("get_next_emi", {"loan_id": "L1004"}, "loan_not_active"),
    ("get_emi_schedule", {"loan_id": "L1001"}, "ok"),
    ("get_overdue_emis", {"loan_id": "L1001"}, "ok"),
    ("get_interest_rate", {"loan_id": "L1002"}, "ok"),
    ("get_interest_certificate", {"loan_id": "L1001", "financial_year": "2025-26"}, "ok"),
    ("get_loan_statement", {"loan_id": "L1001", "start_date": "2026-01-01", "end_date": "2026-09-30"}, "ok"),
    ("get_loan_agreement", {"loan_id": "L1001"}, "ok"),
    ("change_emi_date", {"loan_id": "L1001", "new_day": 10}, "not_eligible"),
    ("change_emi_date", {"loan_id": "L1003", "new_day": 10}, "ok"),
    ("request_noc", {"loan_id": "L1004"}, "ok"),
    ("request_noc", {"loan_id": "L1003"}, "loan_not_closed"),
    ("get_noc_status", {"loan_id": "L1004"}, "ok"),
    # personal
    ("get_personal_loan_foreclosure_quote", {"loan_id": "L1001"}, "ok"),
    ("get_personal_loan_foreclosure_quote", {"loan_id": "L1002"}, "wrong_product"),
    ("request_personal_loan_top_up", {"loan_id": "L1003", "amount": 100000}, "ok"),
    ("request_personal_loan_top_up", {"loan_id": "L1001", "amount": 100000}, "not_eligible"),
    ("get_personal_loan_part_payment_quote", {"loan_id": "L1003", "amount": 20000}, "ok"),
    ("make_personal_loan_part_payment", {"loan_id": "L1003", "amount": 20000}, "ok"),
    # home
    ("get_home_loan_disbursement_status", {"loan_id": "L1002"}, "ok"),
    ("request_home_loan_disbursement", {"loan_id": "L1002", "amount": 500000, "construction_stage": "slab"}, "ok"),
    ("request_home_loan_disbursement", {"loan_id": "L1002", "amount": 9000000, "construction_stage": "slab"}, "invalid_argument"),
    ("get_property_documents_status", {"loan_id": "L1002"}, "ok"),
    ("request_property_documents_return", {"loan_id": "L1002", "collection_branch_id": "B105"}, "loan_not_closed"),
    ("get_home_loan_foreclosure_quote", {"loan_id": "L1002"}, "ok"),
    ("get_home_loan_tax_certificate", {"loan_id": "L1002", "financial_year": "2025-26"}, "ok"),
    ("request_home_loan_balance_transfer_letter", {"loan_id": "L1002"}, "ok"),
    ("get_home_loan_insurance_details", {"loan_id": "L1002"}, "ok"),
    # vehicle
    ("get_vehicle_loan_foreclosure_quote", {"loan_id": "L1005"}, "ok"),
    ("get_hypothecation_status", {"loan_id": "L1006"}, "ok"),
    ("request_hypothecation_removal", {"loan_id": "L1006"}, "ok"),
    ("request_hypothecation_removal", {"loan_id": "L1005"}, "loan_not_closed"),
    ("get_vehicle_insurance_status", {"loan_id": "L1005"}, "ok"),
    ("update_vehicle_insurance", {"loan_id": "L1005", "policy_number": "VI999999", "expiry_date": "2027-10-01"}, "ok"),
    # payments
    ("send_payment_link", {"loan_id": "L1001", "amount": 13538}, "ok"),
    ("get_payment_history", {"loan_id": "L1001"}, "ok"),
    ("get_last_payment", {"loan_id": "L1001"}, "ok"),
    ("check_payment_status", {"payment_id": "P000001"}, "ok"),
    ("report_payment_not_reflected", {"loan_id": "L1001", "amount": 13538, "paid_on": "2026-10-11"}, "ok"),
    ("get_late_fee", {"loan_id": "L1001"}, "ok"),
    ("request_late_fee_waiver", {"loan_id": "L1001", "reason": "hospitalised"}, "ok"),
    ("get_bounce_charges", {"loan_id": "L1001"}, "ok"),
    ("get_autopay_mandate", {"loan_id": "L1001"}, "ok"),
    ("update_autopay_bank_account", {"loan_id": "L1003", "account_number": "123456789012", "ifsc": "HDFC0000456"}, "ok"),
    ("update_autopay_bank_account", {"loan_id": "L1003", "account_number": "12", "ifsc": "HDFC0000456"}, "invalid_argument"),
    ("request_refund", {"loan_id": "L1004", "amount": 2500, "reason": "charged twice"}, "ok"),
    ("get_refund_status", {"loan_id": "L1004"}, "ok"),
    ("pay_emi", {"loan_id": "L1001", "amount": 1}, "amount_mismatch"),
    # collections
    ("record_promise_to_pay", {"loan_id": "L1001", "amount": 13538, "promise_date": "2026-10-20"}, "ok"),
    ("record_promise_to_pay", {"loan_id": "L1001", "amount": 13538, "promise_date": "2027-01-20"}, "invalid_argument"),
    ("get_promise_to_pay", {"loan_id": "L1001"}, "ok"),
    ("request_emi_holiday", {"loan_id": "L1003", "months": 2, "reason": "job loss"}, "ok"),
    ("request_loan_restructuring", {"loan_id": "L1007", "reason": "income dropped"}, "ok"),
    ("get_settlement_offer", {"loan_id": "L1007"}, "ok"),
    ("get_settlement_offer", {"loan_id": "L1001"}, "not_eligible"),
    ("report_financial_hardship", {"customer_id": "C003", "reason": "medical emergency"}, "ok"),
    ("get_collection_call_preferences", {"customer_id": "C001"}, "ok"),
    ("update_collection_call_preferences", {"customer_id": "C001", "preferred_time": "18:00-21:00"}, "invalid_argument"),
    ("update_collection_call_preferences", {"customer_id": "C001", "preferred_time": "10:00-12:00"}, "ok"),
    # new loans
    ("check_personal_loan_eligibility", {"customer_id": "C002", "monthly_income": 140000}, "ok"),
    ("check_home_loan_eligibility", {"customer_id": "C002", "monthly_income": 140000, "property_value": 8000000}, "ok"),
    ("check_vehicle_loan_eligibility", {"customer_id": "C002", "monthly_income": 140000, "vehicle_price": 900000}, "ok"),
    ("get_loan_products", {}, "ok"),
    ("get_current_interest_rates", {"product": "home"}, "ok"),
    ("calculate_emi", {"principal": 500000, "annual_rate": 12, "tenure_months": 36}, "ok"),
    ("create_loan_lead", {"customer_id": "C002", "product": "home", "amount": 5000000}, "ok"),
    ("get_application_status", {"application_id": "A4001"}, "ok"),
    ("send_document_upload_link", {"application_id": "A4001"}, "ok"),
    ("get_preapproved_offers", {"customer_id": "C002"}, "ok"),
    # kyc and profile
    ("get_kyc_status", {"customer_id": "C003"}, "ok"),
    ("start_video_kyc", {"customer_id": "C003"}, "ok"),
    ("start_video_kyc", {"customer_id": "C001"}, "not_needed"),
    ("get_registered_contact_details", {"customer_id": "C001"}, "ok"),
    ("update_address", {"customer_id": "C001", "new_address": "12, Indiranagar 100 Ft Road, Bengaluru - 560038"}, "ok"),
    ("update_phone", {"customer_id": "C001", "new_phone": "9123456789"}, "ok"),
    ("update_phone", {"customer_id": "C001", "new_phone": "12345"}, "invalid_argument"),
    ("update_email", {"customer_id": "C001", "new_email": "ravi.k@example.com"}, "ok"),
    ("update_preferred_language", {"customer_id": "C001", "language": "Kannada"}, "ok"),
    ("update_nominee", {"customer_id": "C002", "nominee_name": "Anil Sharma", "relation": "father"}, "ok"),
    ("link_pan", {"customer_id": "C003", "pan": "cdepr9012m"}, "ok"),
    ("get_document_list", {"product": "home"}, "ok"),
    # cards
    ("get_card_details", {"card_id": "CC2001"}, "ok"),
    ("get_card_bill", {"card_id": "CC2001"}, "ok"),
    ("pay_card_bill", {"card_id": "CC2001", "amount": 5000}, "ok"),
    ("get_card_limit", {"card_id": "CC2001"}, "ok"),
    ("request_card_limit_increase", {"card_id": "CC2001", "new_limit": 200000}, "ok"),
    ("get_card_transactions", {"card_id": "CC2001"}, "ok"),
    ("dispute_card_transaction", {"card_id": "CC2001", "transaction_id": "T600001", "reason": "not mine"}, "ok"),
    ("get_reward_points", {"card_id": "CC2001"}, "ok"),
    ("redeem_reward_points", {"card_id": "CC2001", "points": 100}, "ok"),
    ("send_card_pin_reset_link", {"card_id": "CC2001"}, "ok"),
    # deposits
    ("list_deposits", {"customer_id": "C002"}, "ok"),
    ("get_deposit_details", {"deposit_id": "FD3001"}, "ok"),
    ("get_fd_rates", {}, "ok"),
    ("open_fixed_deposit", {"customer_id": "C002", "amount": 50000, "tenure_months": 12}, "ok"),
    ("open_fixed_deposit", {"customer_id": "C003", "amount": 50000, "tenure_months": 12}, "kyc_required"),
    ("update_fd_maturity_instruction", {"deposit_id": "FD3001", "instruction": "payout"}, "ok"),
    ("get_deposit_interest_certificate", {"deposit_id": "FD3001", "financial_year": "2025-26"}, "ok"),
    # support
    ("faq_search", {"question": "home loan close early charge"}, "ok"),
    ("raise_complaint", {"customer_id": "C001", "category": "wrong_charges", "description": "Charged twice"}, "ok"),
    ("list_complaints", {"customer_id": "C001"}, "ok"),
    ("get_complaint_status", {"complaint_id": "CMP5001"}, "ok"),
    ("book_callback", {"customer_id": "C001", "preferred_time": "tomorrow 11am", "topic": "foreclosure"}, "ok"),
    ("find_nearest_branch", {"pincode": "560001"}, "ok"),
    ("get_branch_timings", {"branch_id": "B101"}, "ok"),
    ("transfer_to_human", {"reason": "wants to talk to a person"}, "ok"),
    ("send_sms_summary", {"customer_id": "C001", "summary": "Your EMI of ₹13,538 is due on 5 Nov"}, "ok"),
    # irreversible / state-changing ones last
    ("pay_emi", {"loan_id": "L1003", "amount": "__next_emi__"}, "ok"),
    ("pay_overdue", {"loan_id": "L1001"}, "ok"),
    ("pay_overdue", {"loan_id": "L1001"}, "nothing_overdue"),
    ("cancel_autopay", {"loan_id": "L1005"}, "ok"),
    ("convert_transaction_to_emi", {"card_id": "CC2001", "transaction_id": "__big_txn__", "tenure_months": 6}, "ok"),
    ("block_card", {"card_id": "CC2001", "reason": "lost"}, "ok"),
    ("block_card", {"card_id": "CC2001", "reason": "lost"}, "card_blocked"),
    ("cancel_loan_application", {"application_id": "A4001"}, "ok"),
    ("accept_settlement_offer", {"loan_id": "L1007", "offer_id": "SO-L1007"}, "ok"),
    ("foreclose_personal_loan", {"loan_id": "L1003"}, "ok"),
    ("foreclose_vehicle_loan", {"loan_id": "L1005"}, "ok"),
    ("foreclose_home_loan", {"loan_id": "L1002"}, "ok"),
    ("break_fixed_deposit", {"deposit_id": "FD3001"}, "ok"),
    ("escalate_complaint", {"complaint_id": "__new_complaint__", "reason": "no response"}, "ok"),
]


def fill_placeholders(args, state):
    out = dict(args)
    if out.get("amount") == "__next_emi__":
        out["amount"] = HANDLERS["get_next_emi"](loan_id=out["loan_id"])["amount"]
    if out.get("transaction_id") == "__big_txn__":
        txns = HANDLERS["get_card_transactions"](card_id=out["card_id"], count=20)["transactions"]
        out["transaction_id"] = next(t["id"] for t in txns if t["amount"] >= 2500 and t["status"] == "posted")
    if out.get("complaint_id") == "__new_complaint__":
        out["complaint_id"] = state["complaint_id"]
    return out


def main():
    with contextlib.redirect_stdout(io.StringIO()):
        seed.main()

    state, passed, failed = {}, 0, []
    for tool, args, expected in CASES:
        args = fill_placeholders(args, state)
        try:
            result = HANDLERS[tool](**args)
            outcome = "ok"
            if tool == "raise_complaint":
                state["complaint_id"] = result["complaint_id"]
        except ToolError as e:
            outcome, result = e.code, e.message
        except Exception as e:  # a real bug
            outcome, result = f"CRASH {type(e).__name__}", str(e)
        if outcome == expected:
            passed += 1
        else:
            failed.append((tool, args, expected, outcome, result))

    untested = sorted(set(HANDLERS) - {c[0] for c in CASES})
    print(f"{passed}/{len(CASES)} cases passed, {len(HANDLERS)} tools, {len(untested)} untested {untested or ''}")
    for tool, args, expected, outcome, result in failed:
        print(f"\nFAIL {tool} {args}\n  expected {expected}, got {outcome}: {result}")

    with contextlib.redirect_stdout(io.StringIO()):
        seed.main()  # leave a clean database behind


if __name__ == "__main__":
    main()
