# Railwise

**Constraint-first, rail-aware AI revenue recovery for UPI AutoPay + cards.**

Razorpay AI Buildathon 2026 — **Track 03: AI Revenue Recovery**

Railwise deepens the decision layer around Razorpay’s existing Intelligent Revenue-Protect / Intelligent Retry work: when merchant-configurable retry templates still leave open questions on **blended rails**, **NPCI-aligned attempt budgets**, and **ambiguous decline codes**, this engine answers with a tested priority order, measured A/B lift, and a full audit trail.

> Positioning: not “Razorpay never thought of smart retries.”  
> Positioning: “here is the hard decision lattice their subscriptions/payments teams already live in — made demonstrable, edge-case complete, and internship-ready.”

## What it does

For every failed recurring debit, Railwise decides one bounded action:

| Action | Meaning |
|---|---|
| `retry_now` | Immediate re-present (only if rail + reason allow) |
| `delayed_retry` | Wait the correct window (UPI cooldown / payday / non-peak) |
| `rail_switch` | Stop hammering dead rail; send payment-link / alt-rail path |
| `dunning` | Customer-action message (logged in demo) |
| `stop` | Hard stop — no further attempts |

**Constraint priority (non-negotiable):**

1. Hard decline / mandate revoked / regulatory block → stop or dunning-only  
2. Attempt budget exhausted (UPI: 1 original + 3 retries) → rail-switch, never another debit  
3. UPI reconciliation cooldown (≥20 min) → delay, never immediate retry  
4. Card over-retry / excess-auth risk → stop burning attempts  
5. Only then may recoverability + timing choose *when* inside the legal window  

## Quick start

Requires **Python 3.11+** (3.12 recommended) and **Node 18+**.

```bash
# Backend
cd backend
python3.12 -m venv .venv          # or python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python data/train_model.py
python data/generator.py
uvicorn app.main:app --reload --port 8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** — run the 500-failure A/B batch, open the edge-case gallery, click the featured failure (`UPI budget exhausted → rail-switch`).

### Tests (USP artifact)

```bash
cd backend && source .venv/bin/activate
pytest tests/edge_cases -q
```

## Repo map

```
railwise/
  backend/
    engine/          # normalize → classify → constraints → policy → execute → audit
    data/            # synthetic generator, fixtures, trained model weights
    app/             # FastAPI + SQLite audit persistence
    tests/edge_cases # named fixtures with expected decisions
  frontend/          # decision cockpit (metrics + reason chain)
  docs/              # ARCHITECTURE, EDGE_CASES, WHY, DEMO_SCRIPT, SECURITY
```

## What makes Railwise different from existing Razorpay Intelligent Retry

Razorpay's existing Intelligent Retry (IR) works at the merchant template level — it allows configuring retry schedules and basic decline-code filtering. Railwise goes four layers deeper:

| Layer | Existing Razorpay IR | Railwise |
|---|---|---|
| Decline taxonomy | Binary: retry / no-retry | ISO 8583 + NPCI: hard / soft / regulatory / ambiguous |
| Rail awareness | Single rail per template | Rail-aware: UPI and card have separate attempt budgets, cooldowns, AFA thresholds |
| Cross-customer intelligence | Per-mandate | **Issuer Health Monitor**: detects SBI/Bandhan outages across all mandates simultaneously |
| Mandate lifecycle | Retry until exhausted | **Mandate Vitality Scorer**: predicts mandate death early, saves retries for live mandates |
| Compliance enforcement | Merchant-configured | Hard-coded NPCI OC/215A/2025-26, RBI CoFT, RBI e-mandate 2026 — unoverridable |
| Audit trail | Execution log | Full constraint chain: *why* each constraint fired, in priority order |

The problems Railwise solves that "no one wants to touch":
1. **Thundering herd on SBI outages** — if 10 000 SBI mandates fail simultaneously, naive retry makes the outage worse. Railwise detects this cross-customer and backs off.
2. **Slow mandate death** — mandates fail for months before anyone notices. Railwise spots a dying mandate after 3 consecutive failures and escalates to dunning before the subscription churns silently.
3. **Token expiry silent failure** — CoFT tokens expire and the card holder doesn't know. Railwise sends a dunning with re-registration link instead of looping failed retries.
4. **PDN gap trap** — RBI mandates 24 h pre-debit notification. Railwise hard-blocks any debit where the notification window was missed, preventing regulatory action.

## Where AI is used (and where it is not)

| Used | Not used |
|---|---|
| Ambiguous decline codes (`do_not_honor`, etc.) → soft/hard + recoverability | Hard declines |
| Timing rank among *already-legal* slots | UPI cooldown / attempt caps |
| | Idempotency / kill switch / stop rules |

Model: pure-Python logistic SGD (`data/models/ambiguous_clf.json`) — interpretable weights, no black-box LLM choosing money actions.

## Measured results (500-event batch, seed 2025)

| Metric | Railwise | Baseline | Delta |
|---|---|---|---|
| Soft recovery rate | **48.4%** | 45.0% | **+3.4 pp** |
| Amount recovered | **₹9,34,818** | ₹8,81,029 | **+₹53,789** |
| Hard wasted retries | **0** | 0 | – |
| UPI cooldown violations | **0** | 0 | – |
| Audit coverage | **100%** | 100%* | – |

*Baseline coverage is execution-logged only; no constraint audit trail.

### New compliance protections (Railwise-only, same batch)

| Guard | Triggered | What it prevented |
|---|---|---|
| Pre-debit notification blocks | 14 | Illegal debits without 24 h customer warning (RBI) |
| Token lifecycle dunnings | 4 | Retrying on expired CoFT tokens (RBI tokenisation) |
| Mandate vitality dunnings | 10 | Wasted retries on near-dead mandates |
| Issuer adaptive backoffs | 134 | Thundering-herd retries during SBI/Bandhan outage |
| Customer-cancelled stops | 9 | Retries after ISO R0/R1 explicit revocation |

### Model quality (logistic SGD, 3 600 samples)

| Metric | Value | Threshold |
|---|---|---|
| Accuracy | **89.3%** | ≥ 72% |
| Soft-decline recall | **96.1%** | ≥ 65% |
| Hard-decline recall | **74.3%** | ≥ 60% |
| Avg recoverability (soft) | **0.677** | > 0.45 |
| Avg recoverability (hard) | **0.187** | < 0.20 |

Top driver (by weight): `prior_hard_declines` → hard; `prior_soft_recoveries` → soft; `issuer_is_sbi` → soft (SBI known false-decline rate).

### Issuer health (same batch)

```
SBI      CRITICAL  TD=40.0%  (40× baseline 0.90%)
BANDHAN  CRITICAL  TD=40.0%  (16× baseline 2.48%)
ICICI    CRITICAL  TD=30.0%  (231× baseline 0.13%)
JIO      CRITICAL  TD=28.6%  (4× baseline 7.23%)
HDFC     HEALTHY   TD=3.3%   (165× baseline — absolute floor not breached)
AXIS     HEALTHY   TD=3.3%   (110× baseline — absolute floor not breached)
```

HDFC and Axis stay HEALTHY because their absolute TD rate (3.3%) never breaches the 10% DEGRADED floor, regardless of relative multiplier. This is by design: ultra-low-baseline banks should not be flagged for incidental technical failures.

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Edge cases](docs/EDGE_CASES.md)
- [Why each file exists](docs/WHY.md)
- [5-minute demo script](docs/DEMO_SCRIPT.md)
- [Security notes](docs/SECURITY.md)
- [Submission checklist](docs/SUBMISSION.md)

## License

Built for Razorpay AI Buildathon 2026 — educational / demonstration use.
