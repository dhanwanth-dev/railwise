# Architecture

## Problem framing

Failed recurring payments are not one category. Soft declines (temporary, recoverable) dominate;
India adds two first-class axes: **rail** (UPI vs Card, with distinct NPCI/RBI rules) and
**issuer** (SBI's 0.90% technical decline rate vs HDFC's 0.02% — same code, very different context).

Razorpay ships Intelligent Revenue-Protect (merchant-configurable retry templates, WhatsApp recovery,
gateway rerouting on latency). Railwise deepens the problem:

- **Issuer-level adaptive backoff** — cross-customer signal detection (thundering herd prevention)
- **Mandate vitality scoring** — proactive pre-failure defense before wasting the last retry
- **Full ISO 8583 + NPCI compliance lattice** — 13 constraint codes, RBI E-mandate 2026, CoFT

## Decision Pipeline

```
Payment failure (Razorpay-shaped webhook)
        │
        ▼
  Ingest & Normalizer       ← rail + issuer_bank + token_id + PDN status + consec. failures
        │
        ▼
  Classification             ← ISO 8583 hard/soft/regulatory; ambiguous → logistic model
        │                       (issuer_bank feature: SBI do_not_honor ≠ HDFC do_not_honor)
        ▼
  Mandate Vitality Scorer    ← LIKELY_DEAD? proactive dunning. AT_RISK? discount recoverability.
        │
        ▼
  Hard Constraint Gate       ← NEVER ML for compliance rules
        │
   forced? ──yes──► STOP / DUNNING / RAIL_SWITCH / DELAYED_RETRY
        │ no
        ▼
  Issuer Health Monitor      ← cross-batch SBI/Bandhan/Jio TD rate signal → adaptive backoff
        │
        ▼
  Policy / Timing            ← choose WHEN inside legal window (issuer-aware + payday-biased)
        │
        ▼
  Execution Adapter          ← simulated; bounded action enum only
        │
        ▼
  Immutable Audit Log        ← full reason chain + new compliance metrics (PDN/token/vitality)
```

## Constraint Priority Order (non-negotiable)

 1. Kill switch → STOP all retries
 2. Mandate revoked → DUNNING only
 3. Token lifecycle failure (RBI CoFT) → DUNNING (re-tokenize)
 4. Regulatory block (RBI AFA / approval) → DUNNING
 5. Customer cancelled recurring (ISO R0/R1) → DUNNING
 6. Pre-debit notification not sent (RBI E-mandate 2026) → DUNNING
 7. Hard decline (ISO 41/43/54/62) → STOP
 8. Attempt budget exhausted (UPI: 4 total; Card: 3 total) → RAIL_SWITCH
 9. UPI re-present too soon (<20 min) → DELAYED_RETRY
10. Velocity limit exhausted (ISO 61/65) → DELAYED_RETRY +24h
11. Amount above ₹15k AFA threshold → DUNNING
12. Issuer systemic failure (cross-customer signal) → DELAYED_RETRY +2h backoff
13. Mandate vitality critical → DUNNING (proactive)

**Compliance always beats recoverability.** That single sentence is the product thesis.

## Components

| Module | Responsibility |
|---|---|
| `engine/normalize.py` | Card/UPI payload → `PaymentFailureEvent` (issuer, token_id, PDN status, consec. failures) |
| `engine/classify.py` | Real ISO 8583 taxonomy; ambiguous → logistic SGD (issuer_bank feature, v2) |
| `engine/issuer_health.py` | **NEW** Cross-customer issuer TD rate monitor → adaptive backoff (thundering herd defense) |
| `engine/mandate_vitality.py` | **NEW** Mandate health scorer → proactive pre-failure dunning |
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

- Soft recovery rate + ₹ recovered (500-event seed 2025: **+17.4 pp**, ₹13.6L vs ₹9.8L)  
- Hard-decline wasted retries (Railwise = 0)  
- UPI cooldown violations (Railwise = 0)  
- Audit coverage (100%)  
- Multi-seed lift **+18.6 pp ± 2.4 pp** (15 × 500)  
