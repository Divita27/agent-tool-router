# Lessons learned

What went wrong while building this, how I noticed, and what I changed. I've kept these because they shaped the design as much as the things that worked.

---

### 1. "It didn't crash" isn't the same as "it's right"
The automated test for all 111 backend actions passed. But reading an actual foreclosure quote showed missed EMIs being counted twice, overcharging by about 20,000 rupees. I fixed the calculation and started reading outputs, not just checking that they existed.

### 2. A vague instruction confused the model
My first system prompt said the caller was "identified from their phone number". The model passed the customer ID into the phone-lookup tool and kept asking the caller for an ID it already had. Stating the phone number and customer ID explicitly fixed it.

### 3. One measurement misled me
After a single run I believed 111 tools made each call about 300 ms slower. Twenty-four runs per size showed about 70 ms. I stopped pitching speed per call and measured whole turns instead, which did turn out 29% faster.

### 4. Generated test data needed checking
I used `sarvam-105b` to write an independent test set. The first batch was mostly unusable: 48 of 71 turns recited the caller's whole background, and none of the "Devanagari" sentences were in Devanagari. Stricter instructions plus automatic checks, with failing items regenerated, fixed it.

### 5. Rate limits are part of the system
Sending four requests at once got "rate limit exceeded". Waiting and retrying with growing pauses fixed it, and the same retry logic went into the agent so a busy API never crashes a call.

### 6. "Yahaan" isn't "yes"
The pattern for "haan" also matched inside "yahaan" ("here"), and हाँ matched inside यहाँ. That would have counted a word that wasn't a yes. I anchored the words properly and added tests for both.

### 7. A prompt couldn't stop double confirmations
The model kept asking "shall I?" itself, and then the safety layer asked again. Telling the model not to ask didn't work. A rule in code did: a yes to the model's own question counts for simple changes, counts for payments only if it stated the exact amount, and never counts for irreversible actions.

### 8. The model got an amount wrong
It offered to pay 27,076 rupees when the real charge, with late fees, was 28,076. Because amounts in read-backs are calculated by code, the caller heard the right figure before anything happened. Money calculations don't belong to the model.

### 9. My scoring was wrong, not the agent
In the end-to-end test, every setup scored 64–74% at first. The same "failures" appeared everywhere, and reading them showed the agent sensibly asking "which loan?" or confirming before a payment. I re-scored from the saved runs, separating good questions from real mistakes, instead of changing the agent to fit a bad metric.

### 10. Short replies need the conversation
"Personal wala" put the right tool at rank 66. Adding the previous turn, with more weight when the reply is four words or fewer, moved it to rank 7, and follow-up replies went from 69% to 94% on the dev set.

### 11. Search can't see prerequisites
"This charge isn't mine" found the dispute tool, but not the transaction list it needs first, because the two don't sound alike. Linking tools that depend on each other rescued 5 of 213 dev turns and broke none.

### 12. Measure before picking a number
I started with K=5 because the papers did. Measuring K from 1 to 20 showed 8 was better: 98.1% vs 96.7% on the independent set, and 96.2% vs 91.4% with 511 tools. An extra tool costs about 107 tokens; a missing one breaks the call.

### 13. The fancier method wasn't better for voice
Asking the model to rewrite unclear requests matched K=8's accuracy, but added a model call on about half the turns, 233 ms typically and 621 ms at worst. Keeping a few more tools did the same job with no wait. Rank fusion (RRF), splitting sentences and picking a topic first also didn't help.

### 14. Test the test
One MCP check failed on its second run: a new tool's embedding was already cached from the first run, so nothing needed re-embedding, and my check expected exactly one. The router was fine; the test's assumption wasn't. I fixed the check and ran everything twice in a row to confirm it was stable.
