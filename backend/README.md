# Fake lending company backend

A pretend lending company ("Demo Lending Co.") with 50 customers, 82 loans, credit cards and fixed deposits. It supports 111 actions, one per tool.

"Today" is fixed at **2026-10-13** (set `LENDING_TODAY` to change it), so due dates and overdue EMIs are the same on every run.

## Commands (from the project folder)

| What | Command |
|---|---|
| Rebuild the database | `python -m backend.seed` |
| Test all 111 tools | `python -m scripts.smoke_backend` |
| Start the API | `uvicorn backend.api:app --reload` |
| Try tools in the browser | open http://127.0.0.1:8000/docs |

## Demo customers

These three are hand-made for tests and the demo video. Everyone else is random but the same on every rebuild.

| Customer | Phone | DOB | Situation |
|---|---|---|---|
| **C001 Ravi Kumar** (Bengaluru, Hindi) | 9876500001 | 1988-04-12 | **L1001** personal loan, EMI ₹13,538, **2 missed EMIs**, plus a UPI payment from 2 days ago that hasn't reflected yet. **L1002** home loan, under construction, ₹27L of ₹45L disbursed. Credit card **CC2001**. |
| **C002 Priya Sharma** (Mumbai, English) | 9876500002 | 1992-09-23 | **L1003** personal loan, all paid on time. **L1004** personal loan, **closed** (can request NOC). **L1005** vehicle loan, active. Fixed deposit **FD3001**. No nominee set. |
| **C003 Arjun Reddy** (Hyderabad, Telugu) | 9876500003 | 1985-01-30 | **L1006** vehicle loan, **closed** (can request hypothecation removal). **L1007** personal loan with **4 missed EMIs** and autopay cancelled (settlement offer available). **KYC pending**. Application **A4001** waiting for documents. |

Look-alike situations on purpose: C001 has two loans ("kaunsa loan?"), C002 has two personal loans, and the home, personal and vehicle loans each have their own foreclosure tool.

## Failure switch

Make a tool fail, to test how the agent recovers:

```bash
curl -X POST localhost:8000/admin/failures -H 'Content-Type: application/json' \
  -d '{"tool": "pay_emi", "mode": "error"}'
```

- `tool`: a tool name, or `"*"` for all tools
- `mode`: `error` (503), `timeout` (waits 10s, then 504), `slow` (waits 3s, then works) or `off`
- `rate`: optional, 0 to 1, e.g. `0.3` fails 30% of calls

`POST /admin/reset` rebuilds the database and turns off all failures.

## Errors

| Status | Meaning | Example |
|---|---|---|
| 400 | Wrong or missing arguments | `missing a required argument: 'loan_id'` |
| 422 | Valid call, but the business rules say no | `Loan L1001 is a personal loan, not a home loan` |
| 503 / 504 | Simulated outage (failure switch) | `The service is temporarily unavailable` |

Data comes back unmasked (full phone numbers, PAN) on purpose. Masking is the safety layer's job (Step 10).
