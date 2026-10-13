"""Every action the fake lending company supports, one function per tool.

Each function takes keyword arguments and returns a dict. Problems are raised as
ToolError so the API can return a clean, speakable error. Data is returned raw
(full phone numbers, PAN, etc.) on purpose: masking is the safety layer's job.
"""
import json
import re
import uuid
from datetime import date, timedelta

from backend.db import TODAY, db

HANDLERS = {}


def handler(fn):
    HANDLERS[fn.__name__] = fn
    return fn


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------- shared helpers ----------

def new_id(prefix):
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


def to_amount(value, name="amount"):
    try:
        amount = round(float(value))
    except (TypeError, ValueError):
        raise ToolError("invalid_argument", f"{name} must be a number")
    if amount <= 0:
        raise ToolError("invalid_argument", f"{name} must be more than zero")
    return amount


def parse_date(value, name="date"):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ToolError("invalid_argument", f"{name} must be a date like 2026-10-25")


def fy_range(financial_year):
    """'2025-26' -> (2025-04-01, 2026-03-31)."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", str(financial_year))
    if not m:
        raise ToolError("invalid_argument", "financial_year must look like 2025-26")
    start = int(m.group(1))
    return date(start, 4, 1), date(start + 1, 3, 31)


def get_customer(conn, customer_id):
    row = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not row:
        raise ToolError("not_found", f"No customer with ID {customer_id}")
    return dict(row)


def get_loan(conn, loan_id, product=None):
    row = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    if not row:
        raise ToolError("not_found", f"No loan with ID {loan_id}")
    loan = dict(row)
    loan["extra"] = json.loads(loan["extra"] or "{}")
    if product and loan["product"] != product:
        raise ToolError("wrong_product", f"Loan {loan_id} is a {loan['product']} loan, not a {product} loan")
    return loan


def require_active(loan):
    if loan["status"] != "active":
        raise ToolError("loan_not_active", f"Loan {loan['id']} is {loan['status']}")


def require_closed(loan):
    if loan["status"] not in ("closed", "settled"):
        raise ToolError("loan_not_closed", f"Loan {loan['id']} is still active. This is only available after closure")


def schedule(conn, loan_id, status=None):
    sql = "SELECT * FROM emi_schedule WHERE loan_id=?"
    params = [loan_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    return [dict(r) for r in conn.execute(sql + " ORDER BY installment_no", params)]


def outstanding_principal(conn, loan):
    """Principal still to be repaid in future EMIs. Missed EMIs are counted separately as overdue."""
    if loan["status"] != "active":
        return 0
    return sum(r["principal_part"] for r in schedule(conn, loan["id"], "pending"))


def overdue_rows(conn, loan_id):
    return schedule(conn, loan_id, "missed")


def late_fee_for(rows):
    return sum(max(500, round(r["amount"] * 0.02)) for r in rows)


def accrued_interest(loan, principal_left):
    """Interest since the last EMI date, used in foreclosure quotes."""
    last_due = TODAY.replace(day=min(loan["emi_day"], 28))
    if last_due > TODAY:
        last_due = (last_due.replace(day=1) - timedelta(days=1)).replace(day=min(loan["emi_day"], 28))
    days = (TODAY - last_due).days
    return round(principal_left * loan["interest_rate"] / 100 * days / 365)


def create_request(conn, type_, customer_id, ref_id, details=None, status="submitted", days=3):
    rid = new_id("R")
    conn.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?)",
                 (rid, customer_id, ref_id, type_, json.dumps(details or {}), status, TODAY.isoformat()))
    return {"request_id": rid, "status": status, "expected_resolution_days": days}


def record_payment(conn, target_type, target_id, amount, method, status="success", note=""):
    pid = new_id("P")
    conn.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?,?)",
                 (pid, target_type, target_id, amount, method, status, TODAY.isoformat(), note))
    return pid


def check_method(method):
    if method not in ("upi", "netbanking", "debit_card"):
        raise ToolError("invalid_argument", "method must be upi, netbanking or debit_card")


FORECLOSURE_CHARGE = {"personal": 0.04, "home": 0.0, "vehicle": 0.03}  # home: no charge on floating-rate loans


def foreclosure_quote(conn, loan_id, product):
    loan = get_loan(conn, loan_id, product)
    require_active(loan)
    principal_left = outstanding_principal(conn, loan)
    missed = overdue_rows(conn, loan_id)
    overdue = sum(r["amount"] for r in missed)
    interest = accrued_interest(loan, principal_left)
    charge = round(principal_left * FORECLOSURE_CHARGE[product])
    gst = round(charge * 0.18)
    total = principal_left + interest + overdue + late_fee_for(missed) + charge + gst
    return {
        "loan_id": loan_id, "principal_outstanding": principal_left, "accrued_interest": interest,
        "overdue_emis": overdue, "late_fees": late_fee_for(missed), "foreclosure_charge": charge, "gst": gst,
        "total_payable": total, "valid_until": (TODAY + timedelta(days=7)).isoformat(),
    }


def foreclose(conn, loan_id, product):
    quote = foreclosure_quote(conn, loan_id, product)
    pid = record_payment(conn, "loan", loan_id, quote["total_payable"], "netbanking", note="Foreclosure")
    conn.execute("UPDATE emi_schedule SET status='paid', paid_on=? WHERE loan_id=? AND status!='paid'",
                 (TODAY.isoformat(), loan_id))
    conn.execute("UPDATE loans SET status='closed', autopay_status='none' WHERE id=?", (loan_id,))
    return {"loan_id": loan_id, "status": "closed", "amount_paid": quote["total_payable"], "payment_id": pid,
            "next_step": "NOC will be issued within 7 working days"}


# =====================================================================
# Customer and identity
# =====================================================================

@handler
def find_customer_by_phone(phone):
    digits = re.sub(r"\D", "", str(phone))[-10:]
    with db() as conn:
        row = conn.execute("SELECT id, name FROM customers WHERE phone=?", (digits,)).fetchone()
    if not row:
        raise ToolError("not_found", "No customer is registered with this phone number")
    return {"customer_id": row["id"], "name": row["name"]}


@handler
def verify_customer(customer_id, dob):
    with db() as conn:
        c = get_customer(conn, customer_id)
    return {"customer_id": customer_id, "verified": c["dob"] == str(parse_date(dob, "dob"))}


@handler
def get_customer_profile(customer_id):
    with db() as conn:
        c = get_customer(conn, customer_id)
    return {k: c[k] for k in ("id", "name", "city", "language", "kyc_status")}


@handler
def list_customer_products(customer_id):
    with db() as conn:
        get_customer(conn, customer_id)
        loans = [dict(r) for r in conn.execute(
            "SELECT id, product, status FROM loans WHERE customer_id=?", (customer_id,))]
        cards = [dict(r) for r in conn.execute(
            "SELECT id, card_type, last4, status FROM credit_cards WHERE customer_id=?", (customer_id,))]
        deposits = [dict(r) for r in conn.execute(
            "SELECT id, amount, status FROM deposits WHERE customer_id=?", (customer_id,))]
    return {"loans": loans, "credit_cards": cards, "deposits": deposits}


# =====================================================================
# Loans (all products)
# =====================================================================

@handler
def list_loans(customer_id):
    with db() as conn:
        get_customer(conn, customer_id)
        rows = conn.execute("SELECT id, product, principal, emi_amount, status FROM loans WHERE customer_id=?",
                            (customer_id,)).fetchall()
    return {"loans": [dict(r) for r in rows]}


@handler
def get_loan_details(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id)
        paid = len(schedule(conn, loan_id, "paid"))
    details = {k: loan[k] for k in ("id", "product", "principal", "interest_rate", "tenure_months",
                                    "emi_amount", "start_date", "emi_day", "status")}
    details["emis_paid"] = paid
    details["emis_remaining"] = 0 if loan["status"] != "active" else loan["tenure_months"] - paid
    return details


@handler
def get_outstanding_amount(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id)
        missed = overdue_rows(conn, loan_id)
        principal_left = outstanding_principal(conn, loan)
    overdue = sum(r["amount"] for r in missed)
    return {"loan_id": loan_id, "principal_outstanding": principal_left, "overdue_amount": overdue,
            "missed_emis": len(missed), "total_to_close_approx": principal_left + overdue}


@handler
def get_next_emi(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_active(loan)
        nxt = schedule(conn, loan_id, "pending")[0]
        missed = overdue_rows(conn, loan_id)
    return {"loan_id": loan_id, "due_date": nxt["due_date"], "amount": nxt["amount"],
            "installment_no": nxt["installment_no"], "missed_emis_before_this": len(missed)}


@handler
def get_emi_schedule(loan_id, count=6):
    with db() as conn:
        get_loan(conn, loan_id)
        rows = [r for r in schedule(conn, loan_id) if r["status"] != "paid"][: int(count)]
    return {"loan_id": loan_id, "upcoming": [{k: r[k] for k in ("installment_no", "due_date", "amount", "status")}
                                             for r in rows]}


@handler
def get_overdue_emis(loan_id):
    with db() as conn:
        get_loan(conn, loan_id)
        rows = overdue_rows(conn, loan_id)
    return {"loan_id": loan_id, "missed_emis": [{k: r[k] for k in ("installment_no", "due_date", "amount")}
                                                for r in rows],
            "total_overdue": sum(r["amount"] for r in rows)}


@handler
def get_interest_rate(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id)
    return {"loan_id": loan_id, "interest_rate": loan["interest_rate"],
            "rate_type": "floating" if loan["product"] == "home" else "fixed"}


@handler
def get_interest_certificate(loan_id, financial_year):
    start, end = fy_range(financial_year)
    with db() as conn:
        get_loan(conn, loan_id)
        rows = [r for r in schedule(conn, loan_id, "paid") if start <= date.fromisoformat(r["due_date"]) <= end]
    return {"loan_id": loan_id, "financial_year": financial_year,
            "interest_paid": sum(r["interest_part"] for r in rows),
            "principal_paid": sum(r["principal_part"] for r in rows),
            "delivery": "PDF sent to registered email"}


@handler
def get_loan_statement(loan_id, start_date, end_date):
    start, end = parse_date(start_date, "start_date"), parse_date(end_date, "end_date")
    if start > end:
        raise ToolError("invalid_argument", "start_date must be before end_date")
    with db() as conn:
        get_loan(conn, loan_id)
    return {"loan_id": loan_id, "start_date": str(start), "end_date": str(end),
            "delivery": "Statement PDF sent to registered email"}


@handler
def get_loan_agreement(loan_id):
    with db() as conn:
        get_loan(conn, loan_id)
    return {"loan_id": loan_id, "delivery": "Loan agreement copy sent to registered email"}


@handler
def change_emi_date(loan_id, new_day):
    new_day = int(new_day)
    if new_day not in (1, 5, 10, 15):
        raise ToolError("invalid_argument", "EMI date can only be the 1st, 5th, 10th or 15th")
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_active(loan)
        if overdue_rows(conn, loan_id):
            raise ToolError("not_eligible", "EMI date can't be changed while EMIs are overdue")
        return create_request(conn, "change_emi_date", loan["customer_id"], loan_id, {"new_day": new_day}, days=5)


@handler
def request_noc(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_closed(loan)
        return create_request(conn, "noc", loan["customer_id"], loan_id, days=7)


@handler
def get_noc_status(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id)
        row = conn.execute("SELECT * FROM requests WHERE ref_id=? AND type='noc' ORDER BY created_at DESC",
                           (loan_id,)).fetchone()
    if row:
        return {"loan_id": loan_id, "status": row["status"], "requested_on": row["created_at"]}
    if loan["status"] == "active":
        return {"loan_id": loan_id, "status": "not_applicable", "reason": "Loan is still active"}
    return {"loan_id": loan_id, "status": "not_requested"}


# =====================================================================
# Personal loans
# =====================================================================

@handler
def get_personal_loan_foreclosure_quote(loan_id):
    with db() as conn:
        return foreclosure_quote(conn, loan_id, "personal")


@handler
def foreclose_personal_loan(loan_id):
    with db() as conn:
        return foreclose(conn, loan_id, "personal")


@handler
def request_personal_loan_top_up(loan_id, amount):
    amount = to_amount(amount)
    with db() as conn:
        loan = get_loan(conn, loan_id, "personal")
        require_active(loan)
        if overdue_rows(conn, loan_id):
            raise ToolError("not_eligible", "Top-up is not available while EMIs are overdue")
        if len(schedule(conn, loan_id, "paid")) < 12:
            raise ToolError("not_eligible", "Top-up needs at least 12 EMIs paid on time")
        return create_request(conn, "top_up", loan["customer_id"], loan_id, {"amount": amount}, days=4)


@handler
def get_personal_loan_part_payment_quote(loan_id, amount):
    amount = to_amount(amount)
    with db() as conn:
        loan = get_loan(conn, loan_id, "personal")
        require_active(loan)
        principal_left = outstanding_principal(conn, loan)
    if amount >= principal_left:
        raise ToolError("invalid_argument", "Amount is more than the outstanding principal. Ask for a foreclosure quote")
    charge = round(amount * 0.02)
    return {"loan_id": loan_id, "part_payment": amount, "charge": charge, "gst": round(charge * 0.18),
            "new_principal_outstanding": principal_left - amount, "effect": "EMI stays the same, tenure reduces"}


@handler
def make_personal_loan_part_payment(loan_id, amount, method="netbanking"):
    quote = get_personal_loan_part_payment_quote(loan_id, amount)
    check_method(method)
    total = quote["part_payment"] + quote["charge"] + quote["gst"]
    with db() as conn:
        pid = record_payment(conn, "loan", loan_id, total, method, note="Part payment")
    return {"loan_id": loan_id, "payment_id": pid, "amount_paid": total, "status": "success",
            "effect": "Tenure will be recalculated within 2 working days"}


# =====================================================================
# Home loans
# =====================================================================

@handler
def get_home_loan_disbursement_status(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id, "home")
    e = loan["extra"]
    return {"loan_id": loan_id, "sanctioned_amount": e["sanctioned_amount"], "disbursed_amount": e["disbursed_amount"],
            "remaining_to_disburse": e["sanctioned_amount"] - e["disbursed_amount"],
            "property_status": e["property_status"]}


@handler
def request_home_loan_disbursement(loan_id, amount, construction_stage):
    amount = to_amount(amount)
    with db() as conn:
        loan = get_loan(conn, loan_id, "home")
        require_active(loan)
        remaining = loan["extra"]["sanctioned_amount"] - loan["extra"]["disbursed_amount"]
        if remaining <= 0:
            raise ToolError("not_eligible", "This loan is already fully disbursed")
        if amount > remaining:
            raise ToolError("invalid_argument", f"Only ₹{remaining} is left to disburse")
        return create_request(conn, "home_disbursement", loan["customer_id"], loan_id,
                              {"amount": amount, "construction_stage": construction_stage}, days=7)


@handler
def get_property_documents_status(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id, "home")
    return {"loan_id": loan_id, "documents_status": loan["extra"]["documents_status"]}


@handler
def request_property_documents_return(loan_id, collection_branch_id):
    with db() as conn:
        loan = get_loan(conn, loan_id, "home")
        require_closed(loan)
        if not conn.execute("SELECT 1 FROM branches WHERE id=?", (collection_branch_id,)).fetchone():
            raise ToolError("not_found", f"No branch with ID {collection_branch_id}")
        return create_request(conn, "property_documents_return", loan["customer_id"], loan_id,
                              {"branch_id": collection_branch_id}, days=15)


@handler
def get_home_loan_foreclosure_quote(loan_id):
    with db() as conn:
        return foreclosure_quote(conn, loan_id, "home")


@handler
def foreclose_home_loan(loan_id):
    with db() as conn:
        result = foreclose(conn, loan_id, "home")
    result["next_step"] = "NOC in 7 working days. Property documents can be collected after 15 days"
    return result


@handler
def get_home_loan_tax_certificate(loan_id, financial_year):
    cert = get_interest_certificate(loan_id, financial_year)
    with db() as conn:
        get_loan(conn, loan_id, "home")
    cert["purpose"] = "Income tax deduction on home loan principal and interest"
    return cert


@handler
def request_home_loan_balance_transfer_letter(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id, "home")
        require_active(loan)
        return create_request(conn, "balance_transfer_letter", loan["customer_id"], loan_id, days=5)


@handler
def get_home_loan_insurance_details(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id, "home")
    return {"loan_id": loan_id, **loan["extra"]["insurance"]}


# =====================================================================
# Vehicle loans
# =====================================================================

@handler
def get_vehicle_loan_foreclosure_quote(loan_id):
    with db() as conn:
        return foreclosure_quote(conn, loan_id, "vehicle")


@handler
def foreclose_vehicle_loan(loan_id):
    with db() as conn:
        result = foreclose(conn, loan_id, "vehicle")
    result["next_step"] = "NOC and Form 35 for hypothecation removal in 7 working days"
    return result


@handler
def get_hypothecation_status(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id, "vehicle")
    e = loan["extra"]
    return {"loan_id": loan_id, "registration_number": e["registration_number"],
            "hypothecation": e["hypothecation"]}


@handler
def request_hypothecation_removal(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id, "vehicle")
        require_closed(loan)
        loan["extra"]["hypothecation"] = "removal_requested"
        conn.execute("UPDATE loans SET extra=? WHERE id=?", (json.dumps(loan["extra"]), loan_id))
        result = create_request(conn, "hypothecation_removal", loan["customer_id"], loan_id, days=10)
    result["next_step"] = "Form 35 and NOC will be sent. Submit them at your RTO"
    return result


@handler
def get_vehicle_insurance_status(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id, "vehicle")
    expiry = date.fromisoformat(loan["extra"]["insurance_expiry"])
    return {"loan_id": loan_id, "policy_number": loan["extra"]["insurance_policy"],
            "expiry": str(expiry), "expired": expiry < TODAY}


@handler
def update_vehicle_insurance(loan_id, policy_number, expiry_date):
    expiry = parse_date(expiry_date, "expiry_date")
    if expiry <= TODAY:
        raise ToolError("invalid_argument", "The new policy's expiry date must be in the future")
    with db() as conn:
        loan = get_loan(conn, loan_id, "vehicle")
        loan["extra"].update(insurance_policy=policy_number, insurance_expiry=str(expiry))
        conn.execute("UPDATE loans SET extra=? WHERE id=?", (json.dumps(loan["extra"]), loan_id))
    return {"loan_id": loan_id, "status": "updated", "policy_number": policy_number, "expiry": str(expiry)}


# =====================================================================
# Payments
# =====================================================================

@handler
def send_payment_link(loan_id, amount):
    amount = to_amount(amount)
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_active(loan)
        pid = record_payment(conn, "loan", loan_id, amount, "link", status="link_sent")
    return {"loan_id": loan_id, "payment_id": pid, "amount": amount,
            "link": f"https://pay.example.com/{pid}", "sent_to": "registered mobile number by SMS",
            "valid_hours": 24}


@handler
def pay_emi(loan_id, amount, method="upi"):
    """Pays the oldest unpaid EMI (missed first, otherwise the next one). Amount must match it."""
    amount = to_amount(amount)
    check_method(method)
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_active(loan)
        target = (overdue_rows(conn, loan_id) or schedule(conn, loan_id, "pending"))[0]
        if amount != target["amount"]:
            raise ToolError("amount_mismatch",
                            f"EMI {target['installment_no']} due on {target['due_date']} is ₹{target['amount']}, "
                            f"not ₹{amount}")
        conn.execute("UPDATE emi_schedule SET status='paid', paid_on=? WHERE loan_id=? AND installment_no=?",
                     (TODAY.isoformat(), loan_id, target["installment_no"]))
        pid = record_payment(conn, "loan", loan_id, amount, method, note=f"EMI {target['installment_no']}")
    return {"loan_id": loan_id, "payment_id": pid, "paid_installment": target["installment_no"],
            "amount_paid": amount, "status": "success"}


@handler
def pay_overdue(loan_id, method="upi"):
    check_method(method)
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_active(loan)
        missed = overdue_rows(conn, loan_id)
        if not missed:
            raise ToolError("nothing_overdue", "There are no overdue EMIs on this loan")
        total = sum(r["amount"] for r in missed) + late_fee_for(missed)
        conn.execute("UPDATE emi_schedule SET status='paid', paid_on=? WHERE loan_id=? AND status='missed'",
                     (TODAY.isoformat(), loan_id))
        pid = record_payment(conn, "loan", loan_id, total, method, note=f"Overdue: {len(missed)} EMIs + late fees")
    return {"loan_id": loan_id, "payment_id": pid, "emis_cleared": len(missed), "amount_paid": total,
            "status": "success"}


@handler
def get_payment_history(loan_id, count=5):
    with db() as conn:
        get_loan(conn, loan_id)
        rows = conn.execute("SELECT id, amount, method, status, created_at, note FROM payments "
                            "WHERE target_id=? ORDER BY created_at DESC LIMIT ?", (loan_id, int(count))).fetchall()
    return {"loan_id": loan_id, "payments": [dict(r) for r in rows]}


@handler
def check_payment_status(payment_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not row:
        raise ToolError("not_found", f"No payment with ID {payment_id}")
    return dict(row)


@handler
def get_last_payment(loan_id):
    history = get_payment_history(loan_id, count=1)["payments"]
    if not history:
        raise ToolError("not_found", "No payments found on this loan")
    return {"loan_id": loan_id, **history[0]}


@handler
def report_payment_not_reflected(loan_id, amount, paid_on, payment_reference=None):
    amount = to_amount(amount)
    paid = parse_date(paid_on, "paid_on")
    with db() as conn:
        loan = get_loan(conn, loan_id)
        pending = conn.execute("SELECT id FROM payments WHERE target_id=? AND status='pending' AND amount=?",
                               (loan_id, amount)).fetchone()
        result = create_request(conn, "payment_not_reflected", loan["customer_id"], loan_id,
                                {"amount": amount, "paid_on": str(paid), "reference": payment_reference}, days=2)
    if pending:
        result["found_pending_payment"] = pending["id"]
        result["note"] = "Payment found and is being reconciled. It should reflect within 48 hours"
    return result


@handler
def get_late_fee(loan_id):
    with db() as conn:
        get_loan(conn, loan_id)
        missed = overdue_rows(conn, loan_id)
    return {"loan_id": loan_id, "missed_emis": len(missed), "late_fee": late_fee_for(missed)}


@handler
def request_late_fee_waiver(loan_id, reason):
    with db() as conn:
        loan = get_loan(conn, loan_id)
        missed = overdue_rows(conn, loan_id)
        if not missed:
            raise ToolError("not_eligible", "There are no late fees on this loan")
        return create_request(conn, "late_fee_waiver", loan["customer_id"], loan_id,
                              {"reason": reason, "fee": late_fee_for(missed)}, status="under_review", days=5)


@handler
def get_bounce_charges(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id)
        missed = overdue_rows(conn, loan_id)
    bounced = len(missed) if loan["autopay_status"] == "active" else 0
    return {"loan_id": loan_id, "bounced_autopay_attempts": bounced, "bounce_charges": bounced * 590}


@handler
def get_autopay_mandate(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id)
    return {"loan_id": loan_id, "autopay_status": loan["autopay_status"],
            "account_last4": loan["autopay_account_last4"], "ifsc": loan["autopay_ifsc"]}


@handler
def update_autopay_bank_account(loan_id, account_number, ifsc):
    digits = re.sub(r"\D", "", str(account_number))
    if not 9 <= len(digits) <= 18:
        raise ToolError("invalid_argument", "Bank account number must have 9 to 18 digits")
    if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", str(ifsc).upper()):
        raise ToolError("invalid_argument", "IFSC must look like SBIN0001234")
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_active(loan)
        result = create_request(conn, "autopay_change", loan["customer_id"], loan_id,
                                {"account_last4": digits[-4:], "ifsc": ifsc.upper()}, days=7)
    result["next_step"] = "Customer must approve the new mandate from the link sent by their bank"
    return result


@handler
def cancel_autopay(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id)
        if loan["autopay_status"] != "active":
            raise ToolError("not_active", "Autopay is not active on this loan")
        conn.execute("UPDATE loans SET autopay_status='cancelled' WHERE id=?", (loan_id,))
    return {"loan_id": loan_id, "autopay_status": "cancelled",
            "warning": "Future EMIs must be paid manually before the due date"}


@handler
def get_refund_status(loan_id):
    with db() as conn:
        get_loan(conn, loan_id)
        rows = conn.execute("SELECT id, details, status, created_at FROM requests WHERE ref_id=? AND type='refund'",
                            (loan_id,)).fetchall()
    if not rows:
        return {"loan_id": loan_id, "refunds": [], "note": "No refund requests on this loan"}
    return {"loan_id": loan_id, "refunds": [{**dict(r), "details": json.loads(r["details"])} for r in rows]}


@handler
def request_refund(loan_id, amount, reason):
    amount = to_amount(amount)
    with db() as conn:
        loan = get_loan(conn, loan_id)
        return create_request(conn, "refund", loan["customer_id"], loan_id,
                              {"amount": amount, "reason": reason}, status="under_review", days=7)


# =====================================================================
# Collections
# =====================================================================

@handler
def record_promise_to_pay(loan_id, amount, promise_date):
    amount = to_amount(amount)
    when = parse_date(promise_date, "promise_date")
    if not TODAY <= when <= TODAY + timedelta(days=30):
        raise ToolError("invalid_argument", "Promise date must be within the next 30 days")
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_active(loan)
        if not overdue_rows(conn, loan_id):
            raise ToolError("nothing_overdue", "There are no overdue EMIs on this loan")
        pid = new_id("PTP")
        conn.execute("INSERT INTO promises VALUES (?,?,?,?,?)", (pid, loan_id, amount, str(when), TODAY.isoformat()))
    return {"promise_id": pid, "loan_id": loan_id, "amount": amount, "promise_date": str(when),
            "note": "Late fees continue to apply until payment"}


@handler
def get_promise_to_pay(loan_id):
    with db() as conn:
        get_loan(conn, loan_id)
        row = conn.execute("SELECT * FROM promises WHERE loan_id=? ORDER BY created_at DESC", (loan_id,)).fetchone()
    if not row:
        return {"loan_id": loan_id, "promise": None}
    return {"loan_id": loan_id, "promise": dict(row)}


@handler
def request_emi_holiday(loan_id, months, reason):
    months = int(months)
    if not 1 <= months <= 3:
        raise ToolError("invalid_argument", "An EMI holiday can be 1 to 3 months")
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_active(loan)
        result = create_request(conn, "emi_holiday", loan["customer_id"], loan_id,
                                {"months": months, "reason": reason}, status="under_review", days=7)
    result["note"] = "Interest continues during the holiday and will be added to the loan"
    return result


@handler
def request_loan_restructuring(loan_id, reason):
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_active(loan)
        return create_request(conn, "restructuring", loan["customer_id"], loan_id, {"reason": reason},
                              status="under_review", days=15)


@handler
def get_settlement_offer(loan_id):
    with db() as conn:
        loan = get_loan(conn, loan_id)
        require_active(loan)
        missed = overdue_rows(conn, loan_id)
        if len(missed) < 3:
            raise ToolError("not_eligible", "Settlement is only offered when 3 or more EMIs are overdue")
        total_due = outstanding_principal(conn, loan) + sum(r["amount"] for r in missed) + late_fee_for(missed)
    return {"loan_id": loan_id, "offer_id": f"SO-{loan_id}", "total_due": total_due,
            "settlement_amount": int(round(total_due * 0.6, -2)), "valid_until": str(TODAY + timedelta(days=15)),
            "warning": "A settled loan is reported to credit bureaus as 'settled', which lowers the credit score"}


@handler
def accept_settlement_offer(loan_id, offer_id, method="netbanking"):
    offer = get_settlement_offer(loan_id)
    if offer_id != offer["offer_id"]:
        raise ToolError("invalid_argument", f"Offer {offer_id} is not valid for this loan")
    check_method(method)
    with db() as conn:
        pid = record_payment(conn, "loan", loan_id, offer["settlement_amount"], method, note="Settlement")
        conn.execute("UPDATE emi_schedule SET status='paid', paid_on=? WHERE loan_id=? AND status!='paid'",
                     (TODAY.isoformat(), loan_id))
        conn.execute("UPDATE loans SET status='settled', autopay_status='none' WHERE id=?", (loan_id,))
    return {"loan_id": loan_id, "status": "settled", "payment_id": pid, "amount_paid": offer["settlement_amount"],
            "credit_report": "Will be reported as settled"}


@handler
def report_financial_hardship(customer_id, reason):
    with db() as conn:
        get_customer(conn, customer_id)
        return create_request(conn, "financial_hardship", customer_id, customer_id, {"reason": reason},
                              status="under_review", days=5)


@handler
def get_collection_call_preferences(customer_id):
    with db() as conn:
        c = get_customer(conn, customer_id)
    return {"customer_id": customer_id, "preferred_call_time": c["preferred_call_time"], "language": c["language"]}


@handler
def update_collection_call_preferences(customer_id, preferred_time, language=None):
    m = re.fullmatch(r"(\d{2}):(\d{2})-(\d{2}):(\d{2})", str(preferred_time))
    if not m:
        raise ToolError("invalid_argument", "preferred_time must look like 10:00-13:00")
    start, end = int(m.group(1)) + int(m.group(2)) / 60, int(m.group(3)) + int(m.group(4)) / 60
    if start < 8 or end > 19 or start >= end:
        raise ToolError("invalid_argument", "Calls are only allowed between 08:00 and 19:00")
    with db() as conn:
        c = get_customer(conn, customer_id)
        conn.execute("UPDATE customers SET preferred_call_time=?, language=? WHERE id=?",
                     (preferred_time, language or c["language"], customer_id))
    return {"customer_id": customer_id, "preferred_call_time": preferred_time, "language": language or c["language"]}


# =====================================================================
# New loans
# =====================================================================

RATES = {"personal": (10.99, 18.0), "home": (8.4, 9.6), "vehicle": (8.8, 11.0)}
DISCLAIMER = "Indicative only. This is not an approval. Final amount depends on credit checks and documents"


def max_principal(emi, annual_rate, months):
    r = annual_rate / 12 / 100
    return round(emi * ((1 + r) ** months - 1) / (r * (1 + r) ** months), -3)


def existing_emis(conn, customer_id):
    return conn.execute("SELECT COALESCE(SUM(emi_amount),0) FROM loans WHERE customer_id=? AND status='active'",
                        (customer_id,)).fetchone()[0]


def eligibility(customer_id, monthly_income, product, months, cap=None):
    income = to_amount(monthly_income, "monthly_income")
    with db() as conn:
        get_customer(conn, customer_id)
        spare_emi = income * 0.5 - existing_emis(conn, customer_id)
    if spare_emi <= 0:
        return {"eligible": False, "reason": "Existing EMIs are already 50% or more of income", "disclaimer": DISCLAIMER}
    amount = max_principal(spare_emi, RATES[product][1], months)
    if cap is not None:
        amount = min(amount, cap)
    return {"eligible": True, "indicative_max_amount": int(amount), "assumed_tenure_months": months,
            "rate_range": RATES[product], "disclaimer": DISCLAIMER}


@handler
def check_personal_loan_eligibility(customer_id, monthly_income):
    return eligibility(customer_id, monthly_income, "personal", 60)


@handler
def check_home_loan_eligibility(customer_id, monthly_income, property_value):
    return eligibility(customer_id, monthly_income, "home", 240, cap=to_amount(property_value, "property_value") * 0.8)


@handler
def check_vehicle_loan_eligibility(customer_id, monthly_income, vehicle_price):
    return eligibility(customer_id, monthly_income, "vehicle", 60, cap=to_amount(vehicle_price, "vehicle_price") * 0.85)


@handler
def get_loan_products():
    return {"products": [
        {"product": "personal", "amount": "₹50,000 to ₹25 lakh", "tenure": "12 to 60 months", "rate": "10.99% to 18%"},
        {"product": "home", "amount": "₹5 lakh to ₹5 crore", "tenure": "up to 30 years", "rate": "8.4% to 9.6%"},
        {"product": "vehicle", "amount": "up to 85% of on-road price", "tenure": "12 to 84 months", "rate": "8.8% to 11%"},
    ]}


@handler
def get_current_interest_rates(product):
    if product not in RATES:
        raise ToolError("invalid_argument", "product must be personal, home or vehicle")
    lo, hi = RATES[product]
    return {"product": product, "min_rate": lo, "max_rate": hi, "note": "Actual rate depends on credit profile"}


@handler
def calculate_emi(principal, annual_rate, tenure_months):
    p = to_amount(principal, "principal")
    rate, months = float(annual_rate), int(tenure_months)
    if not 0 < rate < 40 or not 1 <= months <= 360:
        raise ToolError("invalid_argument", "Rate must be between 0 and 40, tenure between 1 and 360 months")
    r = rate / 12 / 100
    emi = round(p * r * (1 + r) ** months / ((1 + r) ** months - 1))
    return {"emi": emi, "total_interest": emi * months - p, "total_payable": emi * months}


@handler
def create_loan_lead(customer_id, product, amount):
    amount = to_amount(amount)
    if product not in RATES:
        raise ToolError("invalid_argument", "product must be personal, home or vehicle")
    with db() as conn:
        get_customer(conn, customer_id)
        aid = new_id("A")
        conn.execute("INSERT INTO applications VALUES (?,?,?,?,?,?)",
                     (aid, customer_id, product, amount, TODAY.isoformat(), "submitted"))
    return {"application_id": aid, "status": "submitted", "next_step": "A loan officer will call within 1 working day"}


@handler
def get_application_status(application_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM applications WHERE id=?", (application_id,)).fetchone()
    if not row:
        raise ToolError("not_found", f"No application with ID {application_id}")
    return dict(row)


@handler
def send_document_upload_link(application_id):
    app = get_application_status(application_id)
    if app["status"] in ("approved", "rejected", "cancelled"):
        raise ToolError("not_eligible", f"Application is already {app['status']}")
    return {"application_id": application_id, "sent_to": "registered mobile number by SMS", "valid_hours": 48}


@handler
def cancel_loan_application(application_id):
    app = get_application_status(application_id)
    if app["status"] not in ("submitted", "under_review", "documents_pending"):
        raise ToolError("not_eligible", f"Application is {app['status']} and can't be cancelled")
    with db() as conn:
        conn.execute("UPDATE applications SET status='cancelled' WHERE id=?", (application_id,))
    return {"application_id": application_id, "status": "cancelled"}


@handler
def get_preapproved_offers(customer_id):
    with db() as conn:
        get_customer(conn, customer_id)
        loans = conn.execute("SELECT id FROM loans WHERE customer_id=? AND status='active'", (customer_id,)).fetchall()
        any_missed = any(overdue_rows(conn, r["id"]) for r in loans)
    if any_missed:
        return {"customer_id": customer_id, "offers": [], "reason": "No offers while EMIs are overdue"}
    return {"customer_id": customer_id, "offers": [
        {"product": "personal", "max_amount": 300000, "rate": 11.5, "valid_until": str(TODAY + timedelta(days=30))}],
        "disclaimer": DISCLAIMER}


# =====================================================================
# KYC and profile
# =====================================================================

@handler
def get_kyc_status(customer_id):
    with db() as conn:
        c = get_customer(conn, customer_id)
    return {"customer_id": customer_id, "kyc_status": c["kyc_status"]}


@handler
def start_video_kyc(customer_id):
    with db() as conn:
        c = get_customer(conn, customer_id)
        if c["kyc_status"] == "verified":
            raise ToolError("not_needed", "KYC is already verified")
        result = create_request(conn, "video_kyc", customer_id, customer_id, days=1)
    result["next_step"] = "Video KYC link sent by SMS. Keep PAN and Aadhaar ready"
    return result


@handler
def get_registered_contact_details(customer_id):
    with db() as conn:
        c = get_customer(conn, customer_id)
    return {k: c[k] for k in ("phone", "email", "address")}


@handler
def update_address(customer_id, new_address):
    if len(str(new_address).strip()) < 10:
        raise ToolError("invalid_argument", "Please give the full address with pincode")
    with db() as conn:
        get_customer(conn, customer_id)
        result = create_request(conn, "address_change", customer_id, customer_id, {"new_address": new_address},
                                status="pending_verification", days=3)
    result["next_step"] = "Upload address proof from the link sent by SMS"
    return result


@handler
def update_phone(customer_id, new_phone):
    digits = re.sub(r"\D", "", str(new_phone))[-10:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise ToolError("invalid_argument", "Phone number must be a valid 10-digit Indian mobile number")
    with db() as conn:
        get_customer(conn, customer_id)
        result = create_request(conn, "phone_change", customer_id, customer_id, {"new_phone": digits},
                                status="otp_sent", days=1)
    result["next_step"] = "OTP sent to the new number. The change applies after OTP verification"
    return result


@handler
def update_email(customer_id, new_email):
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", str(new_email).lower()):
        raise ToolError("invalid_argument", "That doesn't look like a valid email address")
    with db() as conn:
        get_customer(conn, customer_id)
        result = create_request(conn, "email_change", customer_id, customer_id, {"new_email": new_email},
                                status="verification_sent", days=1)
    result["next_step"] = "Verification link sent to the new email"
    return result


@handler
def update_preferred_language(customer_id, language):
    allowed = {"hindi", "english", "kannada", "tamil", "telugu", "marathi", "bengali", "gujarati", "malayalam",
               "punjabi", "odia"}
    if language.lower() not in allowed:
        raise ToolError("invalid_argument", f"Supported languages: {', '.join(sorted(allowed))}")
    with db() as conn:
        get_customer(conn, customer_id)
        conn.execute("UPDATE customers SET language=? WHERE id=?", (language.lower(), customer_id))
    return {"customer_id": customer_id, "language": language.lower()}


@handler
def update_nominee(customer_id, nominee_name, relation):
    with db() as conn:
        get_customer(conn, customer_id)
        conn.execute("UPDATE customers SET nominee_name=?, nominee_relation=? WHERE id=?",
                     (nominee_name, relation, customer_id))
    return {"customer_id": customer_id, "nominee_name": nominee_name, "relation": relation, "status": "updated"}


@handler
def link_pan(customer_id, pan):
    pan = str(pan).upper().replace(" ", "")
    if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", pan):
        raise ToolError("invalid_argument", "PAN must look like ABCDE1234F")
    with db() as conn:
        get_customer(conn, customer_id)
        conn.execute("UPDATE customers SET pan=? WHERE id=?", (pan, customer_id))
    return {"customer_id": customer_id, "status": "pan_linked"}


@handler
def get_document_list(product):
    common = ["PAN card", "Aadhaar card", "Last 3 months salary slips", "Last 6 months bank statement"]
    extra = {"personal": [], "home": ["Property sale agreement", "Approved building plan", "Title documents"],
             "vehicle": ["Vehicle quotation / proforma invoice"]}
    if product not in extra:
        raise ToolError("invalid_argument", "product must be personal, home or vehicle")
    return {"product": product, "documents": common + extra[product]}


# =====================================================================
# Credit cards
# =====================================================================

def get_card(conn, card_id):
    row = conn.execute("SELECT * FROM credit_cards WHERE id=?", (card_id,)).fetchone()
    if not row:
        raise ToolError("not_found", f"No credit card with ID {card_id}")
    return dict(row)


def require_card_active(card):
    if card["status"] != "active":
        raise ToolError("card_blocked", f"Card ending {card['last4']} is blocked")


@handler
def get_card_details(card_id):
    with db() as conn:
        c = get_card(conn, card_id)
    return {k: c[k] for k in ("id", "card_type", "last4", "status")}


@handler
def get_card_bill(card_id):
    with db() as conn:
        c = get_card(conn, card_id)
    return {"card_id": card_id, "total_due": c["outstanding"], "minimum_due": c["min_due"], "due_date": c["due_date"]}


@handler
def pay_card_bill(card_id, amount, method="upi"):
    amount = to_amount(amount)
    check_method(method)
    with db() as conn:
        c = get_card(conn, card_id)
        if amount > c["outstanding"]:
            raise ToolError("invalid_argument", f"Amount is more than the total due of ₹{c['outstanding']}")
        conn.execute("UPDATE credit_cards SET outstanding=outstanding-?, min_due=MAX(min_due-?,0) WHERE id=?",
                     (amount, amount, card_id))
        pid = record_payment(conn, "card", card_id, amount, method, note="Card bill")
    return {"card_id": card_id, "payment_id": pid, "amount_paid": amount, "remaining_due": c["outstanding"] - amount,
            "status": "success"}


@handler
def get_card_limit(card_id):
    with db() as conn:
        c = get_card(conn, card_id)
    return {"card_id": card_id, "credit_limit": c["credit_limit"], "available_limit": c["credit_limit"] - c["outstanding"]}


@handler
def request_card_limit_increase(card_id, new_limit):
    new_limit = to_amount(new_limit, "new_limit")
    with db() as conn:
        c = get_card(conn, card_id)
        require_card_active(c)
        if new_limit <= c["credit_limit"]:
            raise ToolError("invalid_argument", f"New limit must be above the current limit of ₹{c['credit_limit']}")
        return create_request(conn, "card_limit_increase", c["customer_id"], card_id, {"new_limit": new_limit},
                              status="under_review", days=5)


@handler
def block_card(card_id, reason):
    if reason not in ("lost", "stolen", "fraud", "damaged"):
        raise ToolError("invalid_argument", "reason must be lost, stolen, fraud or damaged")
    with db() as conn:
        c = get_card(conn, card_id)
        require_card_active(c)
        conn.execute("UPDATE credit_cards SET status='blocked' WHERE id=?", (card_id,))
    return {"card_id": card_id, "status": "blocked", "replacement": "New card dispatched in 7 working days"}


@handler
def get_card_transactions(card_id, count=5):
    with db() as conn:
        get_card(conn, card_id)
        rows = conn.execute("SELECT id, date, merchant, amount, status FROM card_transactions WHERE card_id=? "
                            "ORDER BY date DESC LIMIT ?", (card_id, int(count))).fetchall()
    return {"card_id": card_id, "transactions": [dict(r) for r in rows]}


def get_txn(conn, card_id, transaction_id):
    row = conn.execute("SELECT * FROM card_transactions WHERE id=? AND card_id=?", (transaction_id, card_id)).fetchone()
    if not row:
        raise ToolError("not_found", f"No transaction {transaction_id} on this card")
    return dict(row)


@handler
def dispute_card_transaction(card_id, transaction_id, reason):
    with db() as conn:
        c = get_card(conn, card_id)
        t = get_txn(conn, card_id, transaction_id)
        if t["status"] != "posted":
            raise ToolError("not_eligible", f"Transaction is already {t['status']}")
        conn.execute("UPDATE card_transactions SET status='disputed' WHERE id=?", (transaction_id,))
        return create_request(conn, "card_dispute", c["customer_id"], transaction_id,
                              {"reason": reason, "amount": t["amount"]}, status="under_review", days=30)


@handler
def get_reward_points(card_id):
    with db() as conn:
        c = get_card(conn, card_id)
    return {"card_id": card_id, "points": c["reward_points"], "value_in_rupees": round(c["reward_points"] * 0.25)}


@handler
def redeem_reward_points(card_id, points):
    points = int(points)
    with db() as conn:
        c = get_card(conn, card_id)
        if not 0 < points <= c["reward_points"]:
            raise ToolError("invalid_argument", f"You can redeem up to {c['reward_points']} points")
        conn.execute("UPDATE credit_cards SET reward_points=reward_points-?, outstanding=MAX(outstanding-?,0) WHERE id=?",
                     (points, round(points * 0.25), card_id))
    return {"card_id": card_id, "points_redeemed": points, "credited_to_bill": round(points * 0.25)}


@handler
def convert_transaction_to_emi(card_id, transaction_id, tenure_months):
    months = int(tenure_months)
    if months not in (3, 6, 9, 12):
        raise ToolError("invalid_argument", "Tenure must be 3, 6, 9 or 12 months")
    with db() as conn:
        get_card(conn, card_id)
        t = get_txn(conn, card_id, transaction_id)
        if t["amount"] < 2500:
            raise ToolError("not_eligible", "Only transactions of ₹2,500 or more can be converted to EMI")
        if t["status"] != "posted":
            raise ToolError("not_eligible", f"Transaction is already {t['status']}")
        conn.execute("UPDATE card_transactions SET status='converted_to_emi' WHERE id=?", (transaction_id,))
    r = 15 / 12 / 100
    emi = round(t["amount"] * r * (1 + r) ** months / ((1 + r) ** months - 1))
    return {"transaction_id": transaction_id, "tenure_months": months, "monthly_emi": emi, "processing_fee": 199}


@handler
def send_card_pin_reset_link(card_id):
    with db() as conn:
        c = get_card(conn, card_id)
        require_card_active(c)
    return {"card_id": card_id, "sent_to": "registered mobile number by SMS", "valid_minutes": 30}


# =====================================================================
# Deposits
# =====================================================================

FD_RATES = [(12, 7.1), (24, 7.25), (36, 7.5), (60, 7.75)]


def get_deposit(conn, deposit_id):
    row = conn.execute("SELECT * FROM deposits WHERE id=?", (deposit_id,)).fetchone()
    if not row:
        raise ToolError("not_found", f"No deposit with ID {deposit_id}")
    return dict(row)


@handler
def list_deposits(customer_id):
    with db() as conn:
        get_customer(conn, customer_id)
        rows = conn.execute("SELECT id, amount, maturity_date, status FROM deposits WHERE customer_id=?",
                            (customer_id,)).fetchall()
    return {"deposits": [dict(r) for r in rows]}


@handler
def get_deposit_details(deposit_id):
    with db() as conn:
        return get_deposit(conn, deposit_id)


@handler
def get_fd_rates():
    return {"rates": [{"tenure_months": t, "rate": r} for t, r in FD_RATES],
            "senior_citizen_extra": 0.5}


@handler
def open_fixed_deposit(customer_id, amount, tenure_months, method="netbanking"):
    amount = to_amount(amount)
    months = int(tenure_months)
    rate = dict(FD_RATES).get(months)
    if not rate:
        raise ToolError("invalid_argument", "Tenure must be 12, 24, 36 or 60 months")
    if amount < 10000:
        raise ToolError("invalid_argument", "Minimum deposit is ₹10,000")
    check_method(method)
    with db() as conn:
        c = get_customer(conn, customer_id)
        if c["kyc_status"] != "verified":
            raise ToolError("kyc_required", "KYC must be verified before opening a deposit")
        fid = new_id("FD")
        maturity = TODAY + timedelta(days=round(months * 30.4))
        conn.execute("INSERT INTO deposits VALUES (?,?,?,?,?,?,?,?,?)",
                     (fid, customer_id, amount, rate, months, TODAY.isoformat(), maturity.isoformat(),
                      "payout", "active"))
        pid = record_payment(conn, "deposit", fid, amount, method, note="New FD")
    return {"deposit_id": fid, "amount": amount, "rate": rate, "maturity_date": str(maturity), "payment_id": pid}


@handler
def break_fixed_deposit(deposit_id):
    with db() as conn:
        d = get_deposit(conn, deposit_id)
        if d["status"] != "active":
            raise ToolError("not_active", "This deposit is already closed")
        days = (TODAY - date.fromisoformat(d["start_date"])).days
        interest = round(d["amount"] * (d["interest_rate"] - 1) / 100 * days / 365)
        conn.execute("UPDATE deposits SET status='closed' WHERE id=?", (deposit_id,))
    return {"deposit_id": deposit_id, "status": "closed", "principal": d["amount"], "interest_paid": interest,
            "penalty_note": "1% lower interest for premature closure", "payout_days": 2}


@handler
def update_fd_maturity_instruction(deposit_id, instruction):
    allowed = ("renew_principal_and_interest", "renew_principal", "payout")
    if instruction not in allowed:
        raise ToolError("invalid_argument", f"instruction must be one of {', '.join(allowed)}")
    with db() as conn:
        d = get_deposit(conn, deposit_id)
        if d["status"] != "active":
            raise ToolError("not_active", "This deposit is already closed")
        conn.execute("UPDATE deposits SET maturity_instruction=? WHERE id=?", (instruction, deposit_id))
    return {"deposit_id": deposit_id, "maturity_instruction": instruction}


@handler
def get_deposit_interest_certificate(deposit_id, financial_year):
    start, end = fy_range(financial_year)
    with db() as conn:
        d = get_deposit(conn, deposit_id)
    s = max(start, date.fromisoformat(d["start_date"]))
    e = min(end, date.fromisoformat(d["maturity_date"]), TODAY)
    days = max((e - s).days, 0)
    return {"deposit_id": deposit_id, "financial_year": financial_year,
            "interest_earned": round(d["amount"] * d["interest_rate"] / 100 * days / 365),
            "delivery": "Form 16A / interest certificate sent to registered email"}


# =====================================================================
# Support
# =====================================================================

FAQS = [
    ("What is a NOC and when do I get it?", "A No Objection Certificate confirms your loan is fully closed. "
     "It is issued within 7 working days of closure."),
    ("Is there a charge for closing a home loan early?", "No. Floating-rate home loans for individuals have no foreclosure charge."),
    ("What happens if I miss an EMI?", "A late fee applies and it may affect your credit score. Pay as soon as possible."),
    ("How do I change my EMI date?", "EMI date can be changed to the 1st, 5th, 10th or 15th if no EMIs are overdue."),
    ("How long does a refund take?", "Approved refunds reach your bank account in 5 to 7 working days."),
    ("What are your customer care hours?", "Phone support is available 8 AM to 8 PM, all days."),
    ("How is my interest calculated?", "Interest is calculated monthly on the reducing principal balance."),
    ("Can I pay my EMI by UPI?", "Yes. You can pay by UPI, net banking or debit card, or ask for a payment link."),
    ("What documents do I need for a loan?", "PAN, Aadhaar, recent salary slips and bank statements. Home loans need property papers."),
    ("How do I update my KYC?", "You can complete video KYC from a link we send by SMS. Keep PAN and Aadhaar ready."),
    ("How do I block a lost credit card?", "Ask the agent to block the card immediately. A replacement is sent in 7 days."),
    ("What is an EMI holiday?", "A short pause of 1 to 3 months in EMIs. Interest still applies and is added to the loan."),
]


@handler
def faq_search(question):
    words = set(re.findall(r"\w+", str(question).lower()))
    scored = sorted(FAQS, key=lambda qa: -len(words & set(re.findall(r"\w+", qa[0].lower()))))
    return {"results": [{"question": q, "answer": a} for q, a in scored[:2]]}


@handler
def raise_complaint(customer_id, category, description):
    categories = ("payment_not_updated", "wrong_charges", "rude_agent", "noc_delay", "card_issue", "other")
    if category not in categories:
        raise ToolError("invalid_argument", f"category must be one of {', '.join(categories)}")
    with db() as conn:
        get_customer(conn, customer_id)
        cid = new_id("CMP")
        conn.execute("INSERT INTO complaints VALUES (?,?,?,?,?,?,?)",
                     (cid, customer_id, category, description, TODAY.isoformat(), "open", 0))
    return {"complaint_id": cid, "status": "open", "expected_resolution_days": 7}


@handler
def get_complaint_status(complaint_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,)).fetchone()
    if not row:
        raise ToolError("not_found", f"No complaint with ID {complaint_id}")
    return dict(row)


@handler
def list_complaints(customer_id):
    with db() as conn:
        get_customer(conn, customer_id)
        rows = conn.execute("SELECT id, category, status, created_at FROM complaints WHERE customer_id=?",
                            (customer_id,)).fetchall()
    return {"complaints": [dict(r) for r in rows]}


@handler
def escalate_complaint(complaint_id, reason):
    c = get_complaint_status(complaint_id)
    if c["status"] == "resolved":
        raise ToolError("not_eligible", "This complaint is already resolved. Raise a new one instead")
    if c["escalated"]:
        raise ToolError("not_eligible", "This complaint is already escalated")
    with db() as conn:
        conn.execute("UPDATE complaints SET escalated=1 WHERE id=?", (complaint_id,))
    return {"complaint_id": complaint_id, "escalated_to": "Grievance Redressal Officer", "response_within_days": 10,
            "reason": reason}


@handler
def book_callback(customer_id, preferred_time, topic):
    with db() as conn:
        get_customer(conn, customer_id)
        result = create_request(conn, "callback", customer_id, customer_id,
                                {"preferred_time": preferred_time, "topic": topic}, status="scheduled", days=1)
    result["note"] = "Callbacks happen between 08:00 and 19:00"
    return result


@handler
def find_nearest_branch(pincode):
    pincode = str(pincode).strip()
    if not re.fullmatch(r"\d{6}", pincode):
        raise ToolError("invalid_argument", "Pincode must have 6 digits")
    with db() as conn:
        rows = conn.execute("SELECT * FROM branches WHERE substr(pincode,1,3)=?", (pincode[:3],)).fetchall()
    if not rows:
        return {"branches": [], "note": "No branch in this area. Most services are available over the phone"}
    return {"branches": [dict(r) for r in rows]}


@handler
def get_branch_timings(branch_id):
    with db() as conn:
        row = conn.execute("SELECT name, address, timings FROM branches WHERE id=?", (branch_id,)).fetchone()
    if not row:
        raise ToolError("not_found", f"No branch with ID {branch_id}")
    return dict(row)


@handler
def transfer_to_human(reason):
    return {"status": "transferring", "reason": reason, "estimated_wait_seconds": 90}


@handler
def send_sms_summary(customer_id, summary):
    with db() as conn:
        get_customer(conn, customer_id)
    return {"customer_id": customer_id, "status": "sent", "characters": len(str(summary))}
