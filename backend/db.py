"""SQLite database for the fake lending company."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "lending.db"

# "Today" for the fake company. Fix it (LENDING_TODAY=2026-10-13) so benchmark
# runs on different days see exactly the same due dates and overdue EMIs.
TODAY = date.fromisoformat(os.environ.get("LENDING_TODAY", "2026-10-13"))

SCHEMA = """
CREATE TABLE customers (
    id TEXT PRIMARY KEY,
    name TEXT, phone TEXT UNIQUE, email TEXT, dob TEXT, pan TEXT,
    address TEXT, city TEXT, pincode TEXT, language TEXT,
    kyc_status TEXT,                -- verified | pending | expired
    nominee_name TEXT, nominee_relation TEXT,
    preferred_call_time TEXT,       -- e.g. "10:00-13:00"
    monthly_income INTEGER
);

CREATE TABLE loans (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers(id),
    product TEXT,                   -- personal | home | vehicle
    principal INTEGER, interest_rate REAL, tenure_months INTEGER,
    emi_amount INTEGER, start_date TEXT, emi_day INTEGER,
    status TEXT,                    -- active | closed | settled
    autopay_status TEXT,            -- active | cancelled | none
    autopay_account_last4 TEXT, autopay_ifsc TEXT,
    extra TEXT                      -- JSON with product-specific fields
);

CREATE TABLE emi_schedule (
    loan_id TEXT REFERENCES loans(id),
    installment_no INTEGER, due_date TEXT, amount INTEGER,
    principal_part INTEGER, interest_part INTEGER,
    status TEXT,                    -- paid | missed | pending
    paid_on TEXT,
    PRIMARY KEY (loan_id, installment_no)
);

CREATE TABLE payments (
    id TEXT PRIMARY KEY,
    target_type TEXT,               -- loan | card | deposit
    target_id TEXT, amount INTEGER, method TEXT,
    status TEXT,                    -- success | pending | failed | link_sent
    created_at TEXT, note TEXT
);

CREATE TABLE credit_cards (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers(id),
    last4 TEXT, card_type TEXT, credit_limit INTEGER, outstanding INTEGER,
    min_due INTEGER, due_date TEXT, reward_points INTEGER,
    status TEXT                     -- active | blocked
);

CREATE TABLE card_transactions (
    id TEXT PRIMARY KEY,
    card_id TEXT REFERENCES credit_cards(id),
    date TEXT, merchant TEXT, amount INTEGER,
    status TEXT                     -- posted | disputed | converted_to_emi
);

CREATE TABLE deposits (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers(id),
    amount INTEGER, interest_rate REAL, tenure_months INTEGER,
    start_date TEXT, maturity_date TEXT,
    maturity_instruction TEXT,      -- renew_principal_and_interest | renew_principal | payout
    status TEXT                     -- active | closed
);

CREATE TABLE applications (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers(id),
    product TEXT, amount INTEGER, created_at TEXT,
    status TEXT                     -- submitted | under_review | documents_pending | approved | rejected | cancelled
);

CREATE TABLE complaints (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers(id),
    category TEXT, description TEXT, created_at TEXT,
    status TEXT,                    -- open | in_progress | resolved
    escalated INTEGER DEFAULT 0
);

CREATE TABLE promises (
    id TEXT PRIMARY KEY,
    loan_id TEXT REFERENCES loans(id),
    amount INTEGER, promise_date TEXT, created_at TEXT
);

-- Generic service requests: NOC, top-up, EMI holiday, refunds, callbacks, etc.
CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    customer_id TEXT, ref_id TEXT, type TEXT, details TEXT,
    status TEXT, created_at TEXT
);

CREATE TABLE branches (
    id TEXT PRIMARY KEY,
    name TEXT, city TEXT, pincode TEXT, address TEXT, timings TEXT
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db():
    """Open a connection, commit on success, always close."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def create_schema():
    if DB_PATH.exists():
        DB_PATH.unlink()
    with db() as conn:
        conn.executescript(SCHEMA)
