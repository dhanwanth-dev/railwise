# WHY — plain-language map of the codebase

Read this before a panel. You should be able to explain each file in one sentence.

## Backend engine

| File | Why it exists | What breaks if removed |
|---|---|---|
| `normalize.py` | Card and UPI failures arrive in different shapes; one schema makes **rail** first-class | Policy would special-case raw payloads and miss rail rules |
| `classify.py` | Soft vs hard is not always a clean code map; ambiguous codes need context | Either over-retry hard-looking soft codes or under-recover true soft |
| `constraints.py` | Scheme/NPCI rules must never be “learned around” | Compliance violations; double-debit risk |
| `policy.py` | Chooses action + timing **inside** the allowed set | Constraints alone cannot pick payday vs non-peak windows |
| `baseline.py` | Naive foil so metrics are comparable | No honest A/B story for Track 03 bar |
| `execute.py` | Demo needs measured ₹ recovered without live money | Metrics would be decision counts only |
| `audit.py` | Buildathon requires audit trail + stopping rules evidence | Cannot prove why an action happened |
| `pipeline.py` | Single entrypoint for decide/batch | Callers would re-wire layers inconsistently |

## Data

| File | Why |
|---|---|
| `generator.py` | Realistic soft/hard mix (~80% soft) shaped like Razorpay payloads |
| `fixtures.py` | Named edge cases = USP gallery + tests |
| `train_model.py` | Fits ambiguous-code logistic weights offline |
| `models/ambiguous_clf.json` | Auditable weights (no opaque binary) |

## App / UI

| File | Why |
|---|---|
| `app/main.py` | HTTP surface for cockpit + batch A/B |
| `app/db.py` | Append-only decision log (demo-grade immutability) |
| `frontend/App.tsx` | Metrics + reason chain + featured failure walkthrough |

## Mental model for interviews

1. **Ingest** makes rail visible.  
2. **Classify** separates clear rules from ambiguous ML.  
3. **Constraints** are a hard ceiling.  
4. **Policy** only moves inside the ceiling.  
5. **Audit** proves it.

If you remember only one line: **compliance beats recoverability.**
