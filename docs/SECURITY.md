# Security notes (demo-grade, real thinking)

## Boundaries

- No real PANs, VPAs, live API keys, or customer PII in the repo.  
- Synthetic events only; execution adapter **simulates** retries and logs “would send” for dunning/rail-switch.  
- Action set is a closed enum — executor cannot invent money moves.

## Controls

| Control | Implementation |
|---|---|
| Idempotency | `sha256(policy:payment_id:attempt:decline_code)` — duplicate events replay prior decision |
| Kill switch | `STOP_ALL_RETRIES` via `POST /kill-switch?enabled=true` |
| Append-only audit | SQLite rows inserted, never updated in app code |
| Constraint gate before policy | ML cannot override hard declines / cooldowns / budgets |
| Bounded recovery | Attempt caps per rail; diminishing-returns cutoff |

## Threat model (short)

| Threat | Mitigation |
|---|---|
| Over-retry / network abuse | Hard attempt budgets + UPI cooldown |
| Double debit on UPI | Cooldown gate on attempt>1 |
| Model gaming compliance | Constraints evaluated before policy; forced actions win |
| Accidental live send | No WhatsApp/SMS/email providers wired |

## Production follow-ons (not in 10-day scope)

- HSM/KMS for secrets, merchant-scoped auth, signed audit log, Razorpay test-mode executor with real idempotency keys, rate limits per merchant.
