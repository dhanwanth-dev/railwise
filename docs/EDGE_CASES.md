# Edge cases (USP)

Each case is a **named fixture + expected decision + pytest assertion** in `backend/tests/edge_cases/` and `backend/data/fixtures.py`. The gallery in the cockpit is the same set.

| ID | Scenario | Expected |
|---|---|---|
| `hard_decline_stop` | Stolen/lost/blocked card | `stop` · zero retries |
| `card_nsf_payday` | Card insufficient funds | `delayed_retry` · payday-biased |
| `upi_nsf_cooldown` | UPI NSF first attempt | `delayed_retry` · non-peak slot |
| `upi_immediate_represent_blocked` | UPI re-present within minutes | `delayed_retry` · `upi_cooldown` |
| `upi_budget_exhausted_rail_switch` | Attempt 4 + high recoverability | `rail_switch` · budget overrides score |
| `card_over_retry_dunning` | Card attempt ≥3 budget | switch/dunning · no excess auth |
| `ambiguous_with_soft_history` | `do_not_honor` + soft history | contextual soft (model/rules) |
| `ambiguous_no_history_conservative` | `do_not_honor` cold | conservative delay/dunning |
| `mandate_revoked_dunning` | Mandate cancelled mid-sequence | `dunning` only |
| `regulatory_no_retry` | RBI/approval required | `dunning` · no retry queue |
| `network_timeout_short_delay` | Transient timeout | `delayed_retry` |
| `dead_card_alt_upi_switch` | Weak card + live UPI mandate | prefer switch when weak |
| `amount_needs_customer_action` | UPI amount above comfort/AFA | `dunning` |
| *(batch)* | Duplicate payment_id+attempt | idempotent replay |
| *(batch)* | Kill switch on | all `stop` |
| *(batch)* | 300–500 synthetic failures | Railwise: 0 hard waste, 0 UPI violations |

## Why this is the USP

Anyone can draw a “smart retry” box. The internship signal is: **every adversarial case has a priority, a fixture, a test, and an audit line.** Judges can open `pytest tests/edge_cases` and see the system refuse to double-debit UPI or retry stolen cards.
