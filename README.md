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

## Where AI is used (and where it is not)

| Used | Not used |
|---|---|
| Ambiguous decline codes (`do_not_honor`, etc.) → soft/hard + recoverability | Hard declines |
| Timing rank among *already-legal* slots | UPI cooldown / attempt caps |
| | Idempotency / kill switch / stop rules |

Model: pure-Python logistic SGD (`data/models/ambiguous_clf.json`) — interpretable weights, no black-box LLM choosing money actions.

## Demo headline metrics

Same synthetic batch, two policies side-by-side:

- Soft-decline recovery rate (Railwise vs static hourly baseline)
- ₹ recovered delta
- Hard-decline wasted retries (**must be 0** for Railwise)
- UPI cooldown violations (**must be 0** for Railwise)
- Audit coverage (**100%**)

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Edge cases](docs/EDGE_CASES.md)
- [Why each file exists](docs/WHY.md)
- [5-minute demo script](docs/DEMO_SCRIPT.md)
- [Security notes](docs/SECURITY.md)
- [Submission checklist](docs/SUBMISSION.md)

## License

Built for Razorpay AI Buildathon 2026 — educational / demonstration use.
