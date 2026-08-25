# Architecture

## Problem framing

Failed recurring payments are not one category. Soft declines (temporary, recoverable) dominate; India adds a second axis: **rail**. Card network retry heuristics applied to UPI AutoPay risk double-debit and NPCI attempt-cap violations. A blended merchant stack (UPI AutoPay + cards) needs rail as a first-class decision input.

Razorpay already ships Intelligent Revenue-Protect and an Intelligent Retry Engine (merchant-configurable templates, WhatsApp recovery after exhaustion). Railwise deepens the **decision + compliance lattice** those surfaces still leave open under adversarial edge cases.

## Pipeline

```
Payment failure (Razorpay-shaped)
        │
        ▼
  Ingest & Normalizer     ← rail becomes first-class field
        │
        ▼
  Classification          ← rules for clear codes; model only if ambiguous
        │
        ▼
  Hard Constraint Gate    ← NEVER ML; priority order below
        │
   forced? ──yes──► STOP / DUNNING / RAIL_SWITCH / DELAYED_RETRY
        │ no
        ▼
  Policy / Timing         ← choose WHEN inside legal window
        │
        ▼
  Execution Adapter       ← simulated; bounded action enum only
        │
        ▼
  Immutable Audit Log     ← full reason chain + metrics A/B
```

## Constraint priority order

1. Kill switch / hard decline / mandate revoked / regulatory → stop or dunning-only  
2. Attempt budget exhausted → rail-switch (never another debit)  
3. UPI cooldown (≥20 minutes) → delayed retry  
4. Card over-retry risk → dunning / switch  
5. Soft recoverability + timing ranker  

**Compliance always beats recoverability.** That single sentence is the product thesis.

## Components

| Module | Responsibility |
|---|---|
| `engine/normalize.py` | Card vs UPI payload → `PaymentFailureEvent` |
| `engine/classify.py` | Soft/hard/regulatory; ambiguous → logistic model |
| `engine/constraints.py` | Hard gate + NPCI/card ceilings |
| `engine/policy.py` | Action + schedule inside allowed set |
| `engine/baseline.py` | Naive static hourly retry (A/B foil) |
| `engine/execute.py` | Deterministic simulated recovery outcomes |
| `engine/audit.py` | Audit records + batch metrics |
| `engine/pipeline.py` | `decide` / `run_batch` entrypoints |
| `app/main.py` | HTTP API for cockpit |
| `app/db.py` | Append-only SQLite audit store |

## AI placement (defensive)

- **Yes:** ambiguous issuer codes; ranking legal timing slots (payday / non-peak UPI hours).  
- **No:** hard declines, cooldowns, attempt caps, idempotency, kill switch.  

Ambiguous-code model: pure-Python logistic SGD with auditable JSON weights (`backend/data/models/ambiguous_clf.json`) — no LLM chooses money actions.

Showing *where you refused to use a model* is part of the Buildathon evaluation signal.

## Featured failure (demo)

UPI NSF still looks recoverable after attempt 4, but budget is exhausted → **rail_switch**, not another debit. Soft signal loses to hard ceiling. Logged as a distinct decision type with full reason chain.

## Metrics contract

Same synthetic batch (N≥500), Railwise vs `baseline_static`:

- Soft recovery rate + ₹ recovered  
- Hard-decline wasted retries (Railwise = 0)  
- UPI cooldown violations (Railwise = 0)  
- Audit coverage (100%)  
