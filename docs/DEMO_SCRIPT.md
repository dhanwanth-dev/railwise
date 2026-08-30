# 5-minute pitch video script

**Track:** 03 — AI Revenue Recovery  
**Product:** Railwise v2 — constraint-first, issuer-intelligent, mandate-aware recovery

---

## 0:00–0:40 — Problem framing

"Failed recurring payments are a silent revenue leak.

Most systems treat them as one bucket: retry until you hit a limit. That is wrong in three specific ways that no one wants to fix.

First, the **rail matters**. Card retry logic applied to UPI AutoPay burns NPCI attempt budgets — NPCI allows 1 original + 3 representations, full stop.

Second, **issuers go down together**. When SBI's servers have an outage, 50,000 SBI mandates fail at once. A naive system fires all 50,000 retries, worsening the outage. This is called a thundering herd.

Third, **mandates die slowly**. A mandate that has failed 7 consecutive times over 120 days is almost certainly dead — yet most systems keep spending retry attempts on it, then discover too late for dunning to work."

---

## 0:40–1:20 — What Railwise actually does

"Razorpay already ships Intelligent Revenue-Protect and Intelligent Retry — configurable templates, WhatsApp dunning. We are not claiming they missed retries.

Railwise goes four layers deeper:

One — a **constraint-first decision gate** that enforces NPCI OC/215A/2025-26, RBI CoFT tokenisation, and RBI e-mandate 2026 rules before any ML runs.

Two — **Issuer Health Monitor**: detects SBI or Bandhan outages cross-customer in real time, triggers adaptive backoffs for the entire issuer before you make the outage worse.

Three — **Mandate Vitality Scorer**: predicts mandate death from failure history and escalates to dunning early, saving the retry budget for mandates that can actually be saved.

Four — **ISO 8583 decline taxonomy**: 'do_not_honor' from SBI at 2 PM is likely a technical timeout. 'do_not_honor' from HDFC at 3 AM is likely their fraud engine. Same code, different action. A logistic model with 15 auditable features handles this — no black-box LLM choosing money actions."

---

## 1:20–3:20 — Live demo

**Step 1 — Run the batch**

Open the cockpit at `http://localhost:5173`.  
Click **Run 500-failure A/B batch**.

Point at numbers:
- Railwise: **48.4% soft recovery** vs baseline 45.0% — **+3.4 percentage points**
- Railwise recovered **₹9,34,818** vs baseline ₹8,81,029 — **+₹53,789 in one batch**
- Hard wasted retries: **0** (non-negotiable guarantee)
- UPI cooldown violations: **0** (non-negotiable guarantee)
- Audit coverage: **100%**

**Step 2 — New compliance protections (Railwise-only)**

Point at the compliance panel:
- 14 events **blocked** because pre-debit notification wasn't sent (RBI rule)
- 4 **token dunnings** — CoFT tokens had expired, retrying would loop forever
- 10 **mandate vitality dunnings** — caught near-dead mandates before the retry budget ran out
- 134 **issuer adaptive backoffs** — SBI was CRITICAL, we delayed those retries so we didn't make the outage worse

**Step 3 — Issuer health panel**

Show the issuer health table:
- SBI: **CRITICAL** (40% technical decline rate, 44× baseline)
- Bandhan: **CRITICAL** (40% TD, 16× baseline)
- HDFC: **HEALTHY** (3.3% TD — absolute floor not breached)
- Axis: **HEALTHY** (3.3% TD — same)

"HDFC and Axis stay healthy because premium banks have 0.02% normal TD rates. We only flag them if they cross the 10% absolute floor — not just the relative multiplier. This distinction is what prevents false positives."

**Step 4 — Walk a reason chain**

Open the audit for an SBI event that got delayed.  
Show the reason chain:
```
normalize → classify(soft, recov=0.72, source=rules, iso=51) →
constraint: ISSUER_SYSTEMIC_BACKOFF triggered →
action: delayed_retry, delay=45min →
execution_result: recovered
```

---

## 3:20–4:20 — Featured edge case

Open edge case **UPI budget exhausted + high recoverability → rail-switch**.

"Model says recoverability 0.78 — this mandate is genuinely recoverable.  
Constraint says: attempt_number=4, UPI budget exhausted.  
Compliance wins. We cannot place another NPCI debit.  
Action: `rail_switch` — Razorpay sends a payment link to the customer.  
That is the correct decision. No model vote can override it."

Then open **mandate vitality: LIKELY_DEAD**.

"8 consecutive failures. 120 days since last success.  
Constraint: MANDATE_VITALITY_CRITICAL fires.  
We send dunning immediately — WhatsApp + re-registration link.  
We saved 3 retry attempts (which cost per-attempt in production) for mandates that are actually alive."

---

## 4:20–5:00 — AI judgment

"Where is AI used exactly? Two places.

One — ambiguous decline codes. ISO 05 'do_not_honor' goes to a logistic SGD model with 15 auditable features. Top features: `prior_hard_declines` (strong hard signal), `issuer_is_sbi` (high false-decline rate → lean soft), `attempt_number` (diminishing returns). Accuracy: 89.3%. Soft recall: 96.1%.

Two — ranking *already-legal* timing slots. Once compliance says you can retry, AI picks the best slot: payday windows (1st, 7th, 15th, 25th), NPCI non-peak hours, issuer avoidance if degraded.

Hard declines, UPI attempt caps, cooldowns, kill switch — these never get a model vote."

---

## Close

"The edge-case pytest suite is the core artifact: 28 named scenarios, every compliance rule tested, every constraint asserting the expected action. `pytest tests/ -q — 28 passed in 0.3 seconds`."

---

## Recording checklist

- [ ] Show terminal: `python data/train_model.py` → print accuracy 89.3%, soft recall 96.1%
- [ ] Show terminal: `pytest tests/ -q` → 28 passed
- [ ] Run the 500-event A/B batch in the cockpit
- [ ] Show issuer health panel (SBI CRITICAL, HDFC HEALTHY)
- [ ] Walk one full audit reason chain
- [ ] Demo two edge cases: UPI budget exhausted + mandate vitality dead
- [ ] Do not mention any third-party products unrelated to payments
- [ ] Speak the compliance priority order once, slowly, during the featured failure section
