# Walkthroughs

Real runs, step by step. Scores and outputs are copied from actual runs of the code, not written for this page.

---

## 1. "Personal wala": how the router finds the right tool

Ravi has a personal loan and a home loan.

```
Ravi:  Agli EMI kab hai?
Agent: Aapke do loan hain, personal aur home. Kaunsa?
Ravi:  Personal wala
```

The right tool both times is `get_next_emi`. Scores run roughly from 0 to 1, and ranks are out of 111 tools.

**Turn 1: "Agli EMI kab hai?"**

| Step | `get_next_emi` |
|---|---|
| Word score | 1.00, rank 1 |
| Meaning score | 0.83, rank 2 |
| Blend (0.7 × meaning + 0.3 × words) | 0.88, rank 1 |
| Kept in the top 8? | Yes |
| Sent | 13 tools, 1,468 tool tokens instead of 11,895 (88% fewer) |

**Turn 2: "Personal wala"**

| Step | `get_next_emi` |
|---|---|
| Word score | 0.00 (its card doesn't contain "personal") |
| Meaning score | 0.76, rank 66 |
| Blend | 0.53, rank 66 |
| Previous turn's score | 0.90 × 1.5 (short reply) = +1.35 |
| Final | 1.88, **rank 7** |
| Kept in the top 8? | Yes. With a top 5 it would have been missed |
| Linked tools added | `get_overdue_emis`, `pay_emi` |
| Sent | 13 tools, 1,360 tool tokens (89% fewer) |

The model then called `get_next_emi` and replied that the personal-loan EMI is 13,538 rupees, due on 5 November.

---

## 2. The safety layer catches a wrong payment amount

Live run with `sarvam-105b-conversations`. Ravi has two missed EMIs of 13,538 rupees each, plus late fees.

```
Ravi:  Personal loan ki saari pending EMI abhi bhar do
Agent: Do overdue EMI hain, 27,076 rupees total. Kya main inko UPI se pay kar doon?
Ravi:  haan kar do
Agent: Total amount 28,076 rupees hoga, late fees bhi included hai. Kya main payment proceed kar doon?
Ravi:  haan
Agent: Ho gaya, 28,076 rupees ka payment successful ho gaya hai. Aapki dono pending EMI clear ho gayi hain.
```

The model's own question said 27,076 rupees, which left out the 1,000 rupees of late fees. A "yes" to the model's question only counts for a payment if it stated the exact amount the code will charge, and the code calculates that amount itself. So the safety layer held the payment and read back the true figure. Money only moved after Ravi agreed to 28,076.

Audit trail: `confirmation_requested` → `confirmed` → `executed`.

---

## 3. Blocking a card: always a clear warning first

```
Ravi:  Mera credit card kho gaya, block kar do
Agent: Sir, aapka ek Platinum card hai jiske last four digits 0520 hain. Kya isi card ko block karna hai?
Ravi:  haan kar do
Agent: Sir, is card ko permanently block kar diya jayega kyunki yeh kho gaya hai. Naya card aapko bhej diya jayega. Kya main aage badhun?
Ravi:  haan
Agent: Ho gaya sir. Aapka card block kar diya gaya hai. Naya card saat working days ke andar dispatch ho jayega.
```

Blocking a card can't be undone, so a yes to the model's own question isn't enough. The system's own read-back, which explains that the block is permanent, always comes first. A simple change, like moving an EMI date, needs only one confirmation.

---

## 4. A tool added while everything is running (MCP)

The five MCP servers were running, and the router was already serving requests. Then a new tool, `freeze_card_temporarily`, was added to the cards server.

| What happened | Measured |
|---|---|
| Server tells the client its tool list changed | About 0.5 s after the tool was added |
| Router re-indexes | 1 new embedding (only the new tool) |
| Caller: "Mera card do din ke liye band rakhna hai, freeze kar do" | `freeze_card_temporarily` ranked first |
| Calling it through MCP | `{"status": "frozen"}` |
| Safety | No risk level was given, so it needed the caller's yes by default |
| Tool removed again | Gone after the server's next notification |

No restart, and no manual re-wiring.

---

## 5. A full conversation through MCP

Live run with `sarvam-105b-conversations`, with every tool served by the five MCP servers.

```
Ravi:  Agli EMI kab hai?
Agent: Aapke do active loans hain. Personal loan ki EMI 13,538 rupees hai aur home loan ki 39,767 rupees. Aapko kiski due date chahiye?
       (13 tools sent, called list_loans, 4,085 prompt tokens)

Ravi:  Personal wala
Agent: Aapki personal loan ki agli EMI 13,538 rupees hai, jo 5 November ko due hai. Iske pehle do EMIs miss ho chuki hain. Kya aapko in overdue EMIs ki detail bhi chahiye?
       (13 tools sent, called get_next_emi, 4,254 prompt tokens)
```

The router discovered the tools over MCP, shortlisted 13 of 111, and each call went to the server that owns the tool.
