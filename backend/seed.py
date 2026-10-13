"""Fill the database with ~50 fake customers.

Run from the project folder:  python -m backend.seed
Running it again wipes and rebuilds the database. Same seed = same data every time.

Customers C001-C003 are hand-made for demos and tests (see README in backend/).
"""
import json
import random
from datetime import date, timedelta

from faker import Faker

from backend.db import TODAY, create_schema, db

random.seed(42)
fake = Faker("en_IN")
Faker.seed(42)

CITIES = {
    "Mumbai": "400", "Delhi": "110", "Bengaluru": "560", "Chennai": "600",
    "Hyderabad": "500", "Pune": "411", "Kolkata": "700", "Jaipur": "302",
    "Lucknow": "226", "Ahmedabad": "380", "Mysuru": "570", "Indore": "452",
    "Patna": "800", "Bhopal": "462", "Kochi": "682",
}
LANGUAGES = ["hindi"] * 5 + ["english"] * 3 + ["kannada", "tamil", "marathi", "bengali", "telugu"]
MERCHANTS = ["Grocery Mart", "City Fuel Station", "Food Delivery", "Electronics Hub", "Pharmacy Plus",
             "Movie Tickets", "Online Shopping", "Rail Booking", "Mobile Recharge", "Fashion Store"]
VEHICLES = ["Maruti Swift", "Hyundai Creta", "Tata Nexon", "Honda City", "Mahindra XUV300",
            "Honda Activa", "Royal Enfield Classic 350", "Tata Punch"]


# ---------- helpers ----------

def add_months(d, months, day):
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(day, 28))


def emi_amount(principal, annual_rate, months):
    r = annual_rate / 12 / 100
    return round(principal * r * (1 + r) ** months / ((1 + r) ** months - 1))


def rupees(lo, hi, step):
    return random.randrange(lo, hi + 1, step)


class Ids:
    def __init__(self):
        self.n = {}

    def next(self, prefix, start):
        self.n[prefix] = self.n.get(prefix, start - 1) + 1
        return f"{prefix}{self.n[prefix]}"


ids = Ids()
payment_counter = [0]


def payment_id():
    payment_counter[0] += 1
    return f"P{payment_counter[0]:06d}"


# ---------- builders ----------

def insert_customer(conn, c):
    conn.execute(
        "INSERT INTO customers VALUES (:id,:name,:phone,:email,:dob,:pan,:address,:city,:pincode,"
        ":language,:kyc_status,:nominee_name,:nominee_relation,:preferred_call_time,:monthly_income)", c)


def random_customer(cid):
    city = random.choice(list(CITIES))
    pincode = CITIES[city] + f"{random.randint(1, 99):03d}"
    name = fake.name()
    return {
        "id": cid, "name": name,
        "phone": f"9{random.randint(100000000, 999999999)}",
        "email": name.lower().replace(" ", ".") + "@example.com",
        "dob": fake.date_of_birth(minimum_age=23, maximum_age=62).isoformat(),
        "pan": "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5))
               + f"{random.randint(0, 9999):04d}" + random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        "address": f"{fake.building_number()}, {fake.street_name()}, {city} - {pincode}",
        "city": city, "pincode": pincode,
        "language": random.choice(LANGUAGES),
        "kyc_status": random.choices(["verified", "pending", "expired"], [80, 15, 5])[0],
        "nominee_name": random.choice([fake.name(), None]),
        "nominee_relation": random.choice(["spouse", "father", "mother", "son", "daughter"]),
        "preferred_call_time": random.choice(["10:00-13:00", "14:00-17:00", "17:00-19:00"]),
        "monthly_income": rupees(25000, 300000, 5000),
    }


def insert_loan(conn, *, customer_id, product, principal, rate, tenure, months_elapsed,
                missed=0, closed=False, autopay="active", extra=None, emi_day=None):
    """Create a loan and its full EMI schedule.

    months_elapsed: how many EMIs have fallen due before TODAY.
    missed: how many of the most recent past-due EMIs were not paid.
    closed: loan fully paid off (all EMIs paid, possibly foreclosed early).
    """
    loan_id = ids.next("L", 1001)
    emi_day = emi_day or random.choice([1, 5, 10, 15])
    start = add_months(TODAY, -months_elapsed, emi_day)
    if add_months(start, months_elapsed, emi_day) >= TODAY:
        start = add_months(start, -1, emi_day)
    emi = emi_amount(principal, rate, tenure)
    status = "closed" if closed else "active"

    conn.execute(
        "INSERT INTO loans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (loan_id, customer_id, product, principal, rate, tenure, emi, start.isoformat(), emi_day,
         status, "none" if closed else autopay,
         f"{random.randint(0, 9999):04d}" if autopay == "active" else None,
         random.choice(["SBIN0001234", "HDFC0000456", "ICIC0000789", "KKBK0000321"]) if autopay == "active" else None,
         json.dumps(extra or {})))

    balance = principal
    r = rate / 12 / 100
    past_due = [i for i in range(1, tenure + 1) if add_months(start, i, emi_day) < TODAY]
    missed_set = set(past_due[-missed:]) if missed else set()

    for i in range(1, tenure + 1):
        due = add_months(start, i, emi_day)
        interest = round(balance * r)
        principal_part = min(balance, emi - interest) if i < tenure else balance
        amount = principal_part + interest
        balance -= principal_part

        if closed:
            st, paid_on = "paid", min(due, TODAY - timedelta(days=30))
        elif due < TODAY and i not in missed_set:
            st, paid_on = "paid", due
        elif due < TODAY:
            st, paid_on = "missed", None
        else:
            st, paid_on = "pending", None

        conn.execute("INSERT INTO emi_schedule VALUES (?,?,?,?,?,?,?,?)",
                     (loan_id, i, due.isoformat(), amount, principal_part, interest, st,
                      paid_on.isoformat() if paid_on else None))
        if st == "paid" and due < TODAY:
            method = "autopay" if autopay == "active" else random.choice(["upi", "netbanking", "debit_card"])
            conn.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?,?)",
                         (payment_id(), "loan", loan_id, amount, method, "success",
                          paid_on.isoformat(), f"EMI {i}"))
        if closed and due >= TODAY - timedelta(days=30):
            break  # foreclosed: no more rows needed after closure
    return loan_id


def home_extra(principal, closed=False):
    disbursed = principal if (closed or random.random() < 0.7) else round(principal * random.uniform(0.4, 0.8), -4)
    return {
        "sanctioned_amount": principal, "disbursed_amount": int(disbursed),
        "property_address": f"Flat {random.randint(101, 1504)}, {fake.street_name()}",
        "property_status": "ready" if disbursed == principal else "under_construction",
        "documents_status": "ready_for_collection" if closed else "with_lender",
        "insurance": {"provider": "Demo Life Insurance", "policy_number": f"HLI{random.randint(100000, 999999)}",
                      "cover": principal, "expiry": add_months(TODAY, random.randint(12, 200), 1).isoformat()},
    }


def vehicle_extra(closed=False):
    expiry = TODAY + timedelta(days=random.randint(-60, 330))
    return {
        "vehicle_model": random.choice(VEHICLES),
        "registration_number": f"{random.choice(['KA', 'MH', 'DL', 'TN', 'TS', 'UP'])}"
                               f"{random.randint(1, 50):02d}{random.choice('ABCDEFGHJK')}{random.choice('ABCDEFGHJK')}"
                               f"{random.randint(1000, 9999)}",
        "hypothecation": "active",
        "insurance_policy": f"VI{random.randint(100000, 999999)}",
        "insurance_expiry": expiry.isoformat(),
    }


def insert_card(conn, customer_id, **over):
    card_id = ids.next("CC", 2001)
    limit = over.get("credit_limit", rupees(50000, 500000, 10000))
    outstanding = over.get("outstanding", rupees(0, int(limit * 0.8), 100))
    due = TODAY.replace(day=20) if TODAY.day < 20 else add_months(TODAY, 1, 20)
    conn.execute("INSERT INTO credit_cards VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (card_id, customer_id, f"{random.randint(0, 9999):04d}",
                  random.choice(["Platinum", "Gold", "Rewards"]), limit, outstanding,
                  max(round(outstanding * 0.05), 200 if outstanding else 0), due.isoformat(),
                  random.randint(0, 20000), "active"))
    for _ in range(8):
        conn.execute("INSERT INTO card_transactions VALUES (?,?,?,?,?,?)",
                     (ids.next("T", 600001), card_id,
                      (TODAY - timedelta(days=random.randint(1, 30))).isoformat(),
                      random.choice(MERCHANTS), rupees(200, 30000, 50), "posted"))
    return card_id


def insert_deposit(conn, customer_id, amount=None, tenure=None):
    tenure = tenure or random.choice([12, 24, 36, 60])
    start = TODAY - timedelta(days=random.randint(30, 700))
    maturity = add_months(start, tenure, start.day)
    status = "active" if maturity > TODAY else "closed"
    conn.execute("INSERT INTO deposits VALUES (?,?,?,?,?,?,?,?,?)",
                 (ids.next("FD", 3001), customer_id, amount or rupees(25000, 1000000, 5000),
                  random.choice([7.1, 7.25, 7.5, 7.75]), tenure, start.isoformat(), maturity.isoformat(),
                  random.choice(["renew_principal_and_interest", "renew_principal", "payout"]), status))


# ---------- demo customers (fixed, used in tests and the demo video) ----------

def demo_customers(conn):
    # C001: personal loan with 2 missed EMIs + a pending payment, home loan under construction, credit card
    insert_customer(conn, {
        "id": "C001", "name": "Ravi Kumar", "phone": "9876500001", "email": "ravi.kumar@example.com",
        "dob": "1988-04-12", "pan": "ABCPK1234F", "address": "42, MG Road, Bengaluru - 560001",
        "city": "Bengaluru", "pincode": "560001", "language": "hindi", "kyc_status": "verified",
        "nominee_name": "Sunita Kumar", "nominee_relation": "spouse",
        "preferred_call_time": "17:00-19:00", "monthly_income": 95000})
    l1 = insert_loan(conn, customer_id="C001", product="personal", principal=500000, rate=13.5,
                     tenure=48, months_elapsed=14, missed=2, emi_day=5)
    insert_loan(conn, customer_id="C001", product="home", principal=4500000, rate=8.75, tenure=240,
                months_elapsed=18, emi_day=10,
                extra={**home_extra(4500000), "disbursed_amount": 2700000, "property_status": "under_construction"})
    insert_card(conn, "C001", credit_limit=150000, outstanding=42300)
    conn.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?,?)",
                 (payment_id(), "loan", l1, emi_amount(500000, 13.5, 48), "upi", "pending",
                  (TODAY - timedelta(days=2)).isoformat(), "Customer paid by UPI, not yet reconciled"))

    # C002: two personal loans (one closed -> NOC), a vehicle loan, a fixed deposit
    insert_customer(conn, {
        "id": "C002", "name": "Priya Sharma", "phone": "9876500002", "email": "priya.sharma@example.com",
        "dob": "1992-09-23", "pan": "BCDPS5678K", "address": "7, Linking Road, Mumbai - 400050",
        "city": "Mumbai", "pincode": "400050", "language": "english", "kyc_status": "verified",
        "nominee_name": None, "nominee_relation": None,
        "preferred_call_time": "10:00-13:00", "monthly_income": 140000})
    insert_loan(conn, customer_id="C002", product="personal", principal=300000, rate=12.0,
                tenure=36, months_elapsed=20, emi_day=1)
    insert_loan(conn, customer_id="C002", product="personal", principal=200000, rate=14.0,
                tenure=24, months_elapsed=26, closed=True, emi_day=15)
    insert_loan(conn, customer_id="C002", product="vehicle", principal=800000, rate=9.25,
                tenure=60, months_elapsed=9, emi_day=10, extra=vehicle_extra())
    insert_deposit(conn, "C002", amount=200000, tenure=24)

    # C003: closed vehicle loan (hypothecation removal), personal loan with 4 missed EMIs, KYC pending
    insert_customer(conn, {
        "id": "C003", "name": "Arjun Reddy", "phone": "9876500003", "email": "arjun.reddy@example.com",
        "dob": "1985-01-30", "pan": "CDEPR9012M", "address": "15, Banjara Hills, Hyderabad - 500034",
        "city": "Hyderabad", "pincode": "500034", "language": "telugu", "kyc_status": "pending",
        "nominee_name": "Lakshmi Reddy", "nominee_relation": "mother",
        "preferred_call_time": "14:00-17:00", "monthly_income": 60000})
    insert_loan(conn, customer_id="C003", product="vehicle", principal=450000, rate=10.5,
                tenure=36, months_elapsed=38, closed=True, emi_day=5, extra=vehicle_extra(closed=True))
    insert_loan(conn, customer_id="C003", product="personal", principal=250000, rate=16.0,
                tenure=36, months_elapsed=12, missed=4, autopay="cancelled", emi_day=15)
    conn.execute("INSERT INTO applications VALUES (?,?,?,?,?,?)",
                 (ids.next("A", 4001), "C003", "personal", 100000,
                  (TODAY - timedelta(days=6)).isoformat(), "documents_pending"))


# ---------- random customers ----------

def random_customers(conn, count):
    for n in range(4, count + 1):
        cid = f"C{n:03d}"
        insert_customer(conn, random_customer(cid))

        for _ in range(random.choices([1, 2, 3], [55, 35, 10])[0]):
            product = random.choices(["personal", "home", "vehicle"], [55, 20, 25])[0]
            closed = random.random() < 0.15
            missed = 0 if closed else random.choices([0, 1, 2, 3, 4], [70, 12, 8, 6, 4])[0]
            autopay = random.choices(["active", "cancelled", "none"], [75, 10, 15])[0]
            if product == "personal":
                tenure = random.choice([24, 36, 48, 60])
                insert_loan(conn, customer_id=cid, product="personal", principal=rupees(100000, 1500000, 10000),
                            rate=round(random.uniform(11, 18), 2), tenure=tenure,
                            months_elapsed=tenure + 2 if closed else random.randint(2, tenure - 2),
                            missed=missed, closed=closed, autopay=autopay)
            elif product == "home":
                p = rupees(2000000, 9000000, 100000)
                insert_loan(conn, customer_id=cid, product="home", principal=p,
                            rate=round(random.uniform(8.4, 9.6), 2), tenure=random.choice([180, 240, 300]),
                            months_elapsed=random.randint(6, 90), missed=missed, closed=closed,
                            autopay=autopay, extra=home_extra(p, closed))
            else:
                tenure = random.choice([36, 48, 60, 84])
                insert_loan(conn, customer_id=cid, product="vehicle", principal=rupees(300000, 1200000, 10000),
                            rate=round(random.uniform(8.8, 11), 2), tenure=tenure,
                            months_elapsed=tenure + 1 if closed else random.randint(2, tenure - 2),
                            missed=missed, closed=closed, autopay=autopay, extra=vehicle_extra(closed))

        if random.random() < 0.5:
            insert_card(conn, cid)
        for _ in range(random.choices([0, 1, 2], [70, 22, 8])[0]):
            insert_deposit(conn, cid)
        if random.random() < 0.15:
            conn.execute("INSERT INTO applications VALUES (?,?,?,?,?,?)",
                         (ids.next("A", 4001), cid, random.choice(["personal", "home", "vehicle"]),
                          rupees(100000, 5000000, 50000), (TODAY - timedelta(days=random.randint(1, 30))).isoformat(),
                          random.choice(["submitted", "under_review", "documents_pending", "approved", "rejected"])))
        if random.random() < 0.1:
            conn.execute("INSERT INTO complaints VALUES (?,?,?,?,?,?,?)",
                         (ids.next("CMP", 5001), cid,
                          random.choice(["payment_not_updated", "wrong_charges", "rude_agent", "noc_delay"]),
                          "Customer raised an issue over phone", (TODAY - timedelta(days=random.randint(1, 40))).isoformat(),
                          random.choice(["open", "in_progress", "resolved"]), 0))


def branches(conn):
    for city, prefix in CITIES.items():
        for k, area in enumerate(["Main", "East"], start=1):
            conn.execute("INSERT INTO branches VALUES (?,?,?,?,?,?)",
                         (ids.next("B", 101), f"{city} {area} Branch", city, f"{prefix}{k:03d}",
                          f"{fake.building_number()}, {fake.street_name()}, {city}",
                          "Mon-Fri 09:30-17:00, Sat 09:30-13:00"))


def main(count=50):
    # Reset all randomness and counters so every rebuild gives identical data
    random.seed(42)
    Faker.seed(42)
    ids.n.clear()
    payment_counter[0] = 0
    create_schema()
    with db() as conn:
        demo_customers(conn)
        random_customers(conn, count)
        branches(conn)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ["customers", "loans", "emi_schedule", "payments", "credit_cards",
                            "card_transactions", "deposits", "applications", "complaints", "branches"]}
    print(f"Database rebuilt (today = {TODAY}):")
    for t, c in counts.items():
        print(f"  {t:18} {c}")


if __name__ == "__main__":
    main()
