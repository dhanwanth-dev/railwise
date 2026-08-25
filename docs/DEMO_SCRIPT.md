# 5-minute pitch video script

**Track:** 03 — AI Revenue Recovery  
**Product:** Railwise

## 0:00–0:40 — Problem

“Failed recurring payments are usually treated as one bucket: payment failed, retry it. That is wrong.

Soft declines dominate. In India, a second failure mode appears: **rail**. Card hourly retries applied to UPI AutoPay risk double-debit and burn NPCI attempt budgets. Static schedules leave recoverable revenue on the table *and* create compliance risk.”

## 0:40–1:20 — Positioning

“Razorpay already ships Intelligent Revenue-Protect and an Intelligent Retry Engine — configurable templates, WhatsApp recovery after exhaustion. We are not claiming they missed retries.

Railwise deepens the hard part: a **constraint-first decision lattice** for blended UPI + card, where compliance ceilings always beat recoverability scores, ambiguous codes get contextual classification, and every edge case has a tested priority order plus an audit trail.”

## 1:20–3:20 — Live demo

1. Open the cockpit.  
2. Click **Run 500-failure A/B batch**.  
3. Point at metrics: soft recovery lift, ₹ recovered, **0 hard wasted retries**, **0 UPI cooldown violations**, 100% audit coverage.  
4. Open an audit → walk the reason chain (rail → classify → constraint → action).  
5. Show naive baseline still retries hard declines / violates UPI cooldown.

## 3:20–4:20 — Featured failure

Open edge case **UPI budget exhausted + high recoverability → rail-switch**.

“Classification still says recoverable. Attempt budget says stop debiting. Soft signal loses to the hard ceiling — we rail-switch to a payment link instead of another Autopay debit. That is what broke and how we got out.”

## 4:20–5:00 — AI judgment + close

“AI is used only for ambiguous decline codes and ranking *already-legal* timing slots. Hard declines, UPI cooldowns, attempt caps, and the kill switch never get a model vote.

The edge-case pytest suite is the artifact. Happy to walk any fixture in panel.”

## Recording tips

- Show the terminal `pytest tests/edge_cases -q` briefly (15 passed).  
- Do not mention Lorryman or any unrelated apps.  
- Speak the priority order once, slowly.  
