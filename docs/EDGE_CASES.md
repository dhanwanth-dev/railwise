# Edge Cases (USP)

Each case is a **named fixture + expected decision + pytest assertion** in
`backend/tests/edge_cases/` and `backend/data/fixtures.py`. The gallery in
the cockpit shows the same set, live. 28 tests, 21 named fixtures.

## Original Compliance Cases (13)

| ID | Scenario | Expected | Compliance source |
|---|---|---|---|
| `hard_decline_stop` | ISO 43 stolen card | `stop` · zero retries | Scheme rules: never retry hard declines |
| `card_nsf_payday` | ISO 51 NSF, card | `delayed_retry` · payday-biased | Card smart-retry timing |
| `upi_nsf_cooldown` | ISO 51 NSF, UPI first attempt | `delayed_retry` · non-peak slot | NPCI OC/215A non-peak window |
| `upi_immediate_represent_blocked` | UPI re-present in 6 min | `delayed_retry` · `upi_cooldown` | 20-min re-present gap (double-debit prevention) |
| `upi_budget_exhausted_rail_switch` | Attempt 4 + high recoverability | `rail_switch` · `attempt_budget_exhausted` | NPCI OC/215A: max 1 original + 3 retries |
| `card_over_retry_dunning` | Card attempt ≥3 | switch/dunning · `attempt_budget_exhausted` | Visa/MC smart-retry scheme cap |
| `ambiguous_with_soft_history` | ISO 05 `do_not_honor` + SBI + soft history | contextual soft (model) | Issuer-aware model: SBI=high TD bank |
| `ambiguous_no_history_conservative` | ISO 05 `do_not_honor` + HDFC + no history | conservative delay/dunning | HDFC=low TD bank, may be fraud signal |
| `mandate_revoked_dunning` | Mandate cancelled mid-sequence | `dunning` · `mandate_revoked` | Mandate lifecycle |
| `regulatory_no_retry` | RBI approval required | `dunning` · `regulatory_block` | RBI prerequisite |
| `network_timeout_short_delay` | ISO 96 system error | `delayed_retry` | TD: transient |
| `dead_card_alt_upi_switch` | Weak card + live UPI mandate | prefer switch | Alternate rail |
| `amount_needs_customer_action` | UPI >₹15,000 AFA threshold | `dunning` · `amount_needs_customer_action` | RBI E-mandate Framework 2026 |

## New Deep Compliance Cases (8)

| ID | Scenario | Expected | Compliance source |
|---|---|---|---|
| `token_expired_reissue` | Card renewed, old CoFT token invalid | `dunning` · `token_lifecycle_action` | RBI CoFT mandate (mandatory since Oct 2022) |
| `pdn_failed_debit_blocked` | 24h pre-debit notification not sent | `dunning` · `pre_debit_notification_failed` | RBI E-mandate Framework 2026 |
| `r0_customer_cancelled_recurring` | ISO R0: customer told bank to stop recurring | `dunning` · `customer_cancelled_recurring` | Visa/MC R0/R1 recurring stop codes |
| `velocity_limit_24h_window` | ISO 61: daily UPI limit hit | `delayed_retry` · `velocity_limit` · delay ≥1440m | Velocity limit resets at midnight |
| `afa_threshold_1lakh_breach` | Amount >₹1L non-exempt category | `dunning` · `regulatory_block` | RBI AFA requirement above ₹15k |
| `mandate_vitality_dead` | 3 consec. failures + 65 days no success | `dunning` · `mandate_vitality_critical` | Defensive AI: proactive pre-failure |
| `card_technical_issuer_timeout` | ISO 91 (issuer unavailable) | `delayed_retry` · soft classify | Technical decline, not fraud |
| `sbi_do_not_honor_model_soft` | SBI `do_not_honor` + zero history | model → soft preferred | Issuer-aware model: SBI high-TD heuristic |

## Batch Invariants (7)

| Test | What it proves |
|---|---|
| `test_batch_railwise_zero_hard_wasted` | Railwise: 0 hard-decline wasted retries across 300+ events |
| `test_batch_railwise_zero_upi_violations` | Railwise: 0 UPI cooldown violations |
| `test_batch_baseline_has_violations` | Naive baseline: has violations (A/B contrast) |
| `test_railwise_beats_baseline_recovery` | Railwise recovers ≥88% of baseline ₹ (typically much more) |
| `test_idempotent_replay` | Same event processed twice = same key, `idempotent_replay` in chain |
| `test_kill_switch` | Kill switch → all `stop` with `KILL_SWITCH` constraint |
| `test_batch_railwise_new_metrics_counted` | PDN/token/vitality/cancelled metrics computed in batch |

## Unit Tests — Intelligent Defensive AI (4)

| Test | What it proves |
|---|---|
| `test_mandate_vitality_healthy` | Single failure with recovery history → HEALTHY |
| `test_mandate_vitality_likely_dead` | 4 consec. fails + 75 days no success → LIKELY_DEAD + score ≥5.0 |
| `test_issuer_health_monitor_adaptive_backoff` | 10/30 SBI technical = CRITICAL → 120m backoff |
| `test_issuer_health_monitor_hdfc_normal` | 30/30 HDFC business declines → HEALTHY |

## Model Quality Test (1)

| Test | Threshold |
|---|---|
| `test_model_quality_thresholds` | Accuracy ≥72%, hard recall ≥55%, soft recov > hard recov |

## Why This Is the USP

Anyone can draw a "smart retry" box. The Buildathon signal is:
**every adversarial case has a real compliance source, a fixture, a test, and an audit line.**

Judges can open `pytest tests/edge_cases -v` and see the system:
- refuse to double-debit UPI (re-present gap)
- refuse to retry stolen cards (hard stop)
- comply with PDN requirement before debit (RBI rule)
- block retries when token is stale (RBI CoFT)
- honour customer-cancelled recurring (ISO R0/R1)
- apply adaptive backoff when SBI is overloaded (cross-customer signal)
- proactively dunning before wasting the last retry on a dying mandate
