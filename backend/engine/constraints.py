"""
Hard Constraint Gate — NEVER ML for compliance rules.

Priority order (non-negotiable, from highest to lowest):
 1. Kill switch → STOP everything
 2. Mandate revoked → DUNNING only (no further debit retries)
 3. Regulatory block (RBI AFA/approval required) → DUNNING
 4. Customer explicitly cancelled recurring (R0/R1) → DUNNING
 5. Pre-debit notification NOT sent → DUNNING + send PDN first
    Source: RBI Digital Payments E-mandate Framework 2026 (April 21, 2026)
 6. Token lifecycle failure (CoFT expired/invalid) → DUNNING (re-tokenize)
    Source: RBI CoFT Mandate, mandatory since October 1, 2022
 7. Hard decline (stolen/lost/expired/VPA-not-found) → STOP
 8. Attempt budget exhausted:
    UPI: 1 original + 3 retries (NPCI OC/215A/2025-26) → RAIL_SWITCH
    Card: 1 original + 2 retries (scheme smart-retry guidelines) → DUNNING
 9. UPI re-presentation too soon → DELAYED_RETRY (prevent double-debit)
10. Velocity limit exceeded (UPI daily cap) → DELAYED_RETRY exactly 24h
11. Amount needs customer action (>₹15,000 UPI comfort threshold) → DUNNING
12. Issuer systemic failure (cross-customer signal) → DELAYED_RETRY with backoff
13. Mandate vitality critical → DUNNING (proactive, before wasting retry)

The key rule: compliance and scheme rules ALWAYS precede AI recoverability scores.
"""

from __future__ import annotations

from engine.classify import CUSTOMER_CANCELLED_CODES, TOKEN_LIFECYCLE_CODES, VELOCITY_CODES
from engine.issuer_health import get_monitor
from engine.mandate_vitality import score_mandate_vitality
from engine.schemas import (
    Action,
    ClassificationResult,
    ConstraintCode,
    ConstraintHit,
    DeclineKind,
    IssuerHealthLevel,
    MandateVitalityLevel,
    PaymentFailureEvent,
    Rail,
)

# ── NPCI OC/215A/2025-26: UPI AutoPay attempt limits ────────────────────────
# "A maximum of 1 attempt, with 3 retries per mandate"
UPI_MAX_ATTEMPTS = 4          # original + 3 retries = 4 total

# ── Card scheme smart-retry cap ─────────────────────────────────────────────
# Visa/Mastercard allow 1 original + 2 retries within 30 days.
# Exceeding this risks scheme fines for "excessive retries".
CARD_MAX_SMART_ATTEMPTS = 3   # original + 2 retries

# ── UPI re-presentation gap ──────────────────────────────────────────────────
# Prevents immediate re-debit before the previous transaction's settlement
# status is confirmed. 20 minutes is a conservative buffer above the NPCI-
# mandated 90-second check-status wait. Named "represent" to distinguish from
# a scheme-level cooldown.
UPI_MIN_REPRESENT_GAP_MINUTES = 20.0

# ── RBI e-mandate thresholds (E-mandate Framework 2026) ─────────────────────
# Recurring transactions up to ₹15,000: no per-transaction AFA required.
# Above ₹15,000: must collect AFA from customer (treat as dunning, not auto-retry).
# Exception categories (up to ₹1L without AFA): insurance, MF, credit card bill.
UPI_AUTOPAY_AFA_THRESHOLD_PAISE = 15_000_00   # ₹15,000 in paise
UPI_AUTOPAY_EXEMPT_THRESHOLD_PAISE = 1_00_000_00  # ₹1,00,000 in paise


def apply_forced_action(hit: ConstraintHit, event: PaymentFailureEvent, classification: ClassificationResult) -> ConstraintHit:
    """Resolve forced action from a constraint hit, adjusting for context if needed."""
    if hit.forced_action == Action.RAIL_SWITCH and not event.has_alt_card and not event.has_alt_upi_mandate:
        # No alternate rail available → downgrade to dunning (payment link)
        return hit.model_copy(update={
            "forced_action": Action.DUNNING,
            "message": hit.message + " [no alternate rail → payment link dunning]",
        })
    return hit


def evaluate_constraints(
    event: PaymentFailureEvent,
    classification: ClassificationResult,
    *,
    kill_switch: bool = False,
) -> list[ConstraintHit]:
    hits: list[ConstraintHit] = []

    # ── 1. Kill switch ──────────────────────────────────────────────────────
    if kill_switch:
        hits.append(ConstraintHit(
            code=ConstraintCode.KILL_SWITCH,
            message="STOP_ALL_RETRIES flag engaged — emergency halt on all payment retries",
            forced_action=Action.STOP,
        ))
        return hits

    # ── 2. Mandate revoked ──────────────────────────────────────────────────
    if event.mandate_revoked or event.decline_code in ("mandate_revoked", "mandate_cancelled", "token_cancelled"):
        hits.append(ConstraintHit(
            code=ConstraintCode.MANDATE_REVOKED,
            message="Mandate revoked mid-sequence — stop debit retries; customer win-back dunning only",
            forced_action=Action.DUNNING,
        ))
        return hits

    # ── 3. Token lifecycle failure (RBI CoFT) — checked BEFORE generic regulatory ─
    # Token codes are classified as REGULATORY in classify.py, so we must intercept
    # them here BEFORE the generic regulatory block to give them the specific constraint code.
    if event.decline_code in TOKEN_LIFECYCLE_CODES:
        hits.append(ConstraintHit(
            code=ConstraintCode.TOKEN_LIFECYCLE_ACTION,
            message=f"Card token invalid/expired ({event.decline_code}) — RBI CoFT mandate: customer must re-tokenize renewed card at merchant",
            forced_action=Action.DUNNING,
        ))
        return hits

    # ── 4. Regulatory block ─────────────────────────────────────────────────
    if classification.decline_kind == DeclineKind.REGULATORY or event.decline_code in (
        "rbi_approval_required", "approval_required", "authentication_required",
        "1A", "additional_authentication_required", "afa_required",
    ):
        hits.append(ConstraintHit(
            code=ConstraintCode.REGULATORY_BLOCK,
            message="RBI/regulatory prerequisite unmet (AFA/approval required) — timing cannot fix this; customer must act",
            forced_action=Action.DUNNING,
        ))
        return hits

    # ── 5. Customer explicitly cancelled recurring (R0/R1) ──────────────────
    # ISO 8583 R0/R1: "Recurring charge stopped at cardholder request"
    # This is NOT a temporary failure. Customer told their bank to stop all recurring
    # debits from this merchant. NEVER retry — only win-back dunning.
    if event.decline_code in CUSTOMER_CANCELLED_CODES or (event.decline_iso_code or "").upper() in {"R0", "R1"}:
        hits.append(ConstraintHit(
            code=ConstraintCode.CUSTOMER_CANCELLED_RECURRING,
            message="Customer explicitly cancelled recurring mandate (ISO R0/R1) — retrying violates customer intent; win-back dunning only",
            forced_action=Action.DUNNING,
        ))
        return hits

    # ── 5. Pre-debit notification (PDN) not sent ────────────────────────────
    # RBI E-mandate Framework 2026: 24-hour pre-debit notification is MANDATORY
    # for all recurring payments. If the notification wasn't sent/confirmed, the
    # debit cannot proceed — it would be an unauthorized transaction.
    if not event.pre_debit_notification_sent:
        hits.append(ConstraintHit(
            code=ConstraintCode.PRE_DEBIT_NOTIFICATION_FAILED,
            message="RBI e-mandate Framework 2026: 24h pre-debit notification not confirmed — debit cannot proceed; send PDN then retry after 24h",
            forced_action=Action.DUNNING,
            min_delay_minutes=1440.0,  # 24 hours = time to resend PDN + confirmation window
        ))
        return hits

    # ── 6. Hard decline ─────────────────────────────────────────────────────
    if classification.decline_kind == DeclineKind.HARD:
        hits.append(ConstraintHit(
            code=ConstraintCode.HARD_DECLINE,
            message=f"Hard decline ({event.decline_code}) — scheme rules: retrying a hard code wastes attempts and risks fines",
            forced_action=Action.STOP,
        ))
        return hits

    # ── 8. Attempt budget exhausted ──────────────────────────────────────────
    if event.rail == Rail.UPI and event.attempt_number >= UPI_MAX_ATTEMPTS:
        hit = ConstraintHit(
            code=ConstraintCode.ATTEMPT_BUDGET_EXHAUSTED,
            message=f"UPI attempt budget exhausted (attempt {event.attempt_number}/{UPI_MAX_ATTEMPTS}) — NPCI OC/215A: no more AutoPay debits; rail-switch to payment link",
            forced_action=Action.RAIL_SWITCH,
        )
        hits.append(apply_forced_action(hit, event, classification))
        hits[-1].overrides_recoverability = True
        hits[-1].message += " | priority: compliance_over_recoverability"
        return hits

    if event.rail == Rail.CARD and event.attempt_number >= CARD_MAX_SMART_ATTEMPTS:
        hit = ConstraintHit(
            code=ConstraintCode.ATTEMPT_BUDGET_EXHAUSTED,
            message=f"Card attempt budget exhausted (attempt {event.attempt_number}/{CARD_MAX_SMART_ATTEMPTS}) — scheme rules: excessive retries risk fines; dunning or rail-switch",
            forced_action=Action.RAIL_SWITCH,
        )
        hits.append(apply_forced_action(hit, event, classification))
        return hits

    # ── 9. UPI re-presentation too soon ─────────────────────────────────────
    if (
        event.rail == Rail.UPI
        and event.attempt_number > 1
        and event.hours_since_last_attempt * 60 < UPI_MIN_REPRESENT_GAP_MINUTES
    ):
        gap_actual = event.hours_since_last_attempt * 60
        hits.append(ConstraintHit(
            code=ConstraintCode.UPI_COOLDOWN,
            message=f"UPI re-presentation {gap_actual:.0f}min after last attempt — minimum {UPI_MIN_REPRESENT_GAP_MINUTES:.0f}min gap required to prevent double-debit during reconciliation",
            forced_action=Action.DELAYED_RETRY,
            min_delay_minutes=UPI_MIN_REPRESENT_GAP_MINUTES - gap_actual + 5,
        ))
        return hits

    # ── 10. Velocity / daily limit exhausted ────────────────────────────────
    # UPI daily transaction limit resets at midnight. Force exact 24h delay.
    if event.decline_code in VELOCITY_CODES or (event.decline_iso_code or "") in {"61", "65"}:
        hits.append(ConstraintHit(
            code=ConstraintCode.VELOCITY_LIMIT,
            message="Customer hit daily UPI transaction limit — retry exactly after 24h when limit resets at midnight",
            forced_action=Action.DELAYED_RETRY,
            min_delay_minutes=1440.0,  # exactly 24 hours
        ))
        return hits

    # ── 11. Amount needs customer action (AFA threshold) ────────────────────
    if event.rail == Rail.UPI and event.amount_paise > UPI_AUTOPAY_AFA_THRESHOLD_PAISE:
        hits.append(ConstraintHit(
            code=ConstraintCode.AMOUNT_NEEDS_CUSTOMER_ACTION,
            message=f"UPI amount ₹{event.amount_paise // 100:,} exceeds ₹15,000 AFA threshold — blind AutoPay retry not allowed; customer must confirm",
            forced_action=Action.DUNNING,
        ))
        return hits

    # ── 12. Issuer systemic failure (adaptive backoff) ────────────────────────
    # Cross-customer signal: if this issuer's TD rate is spiking, adaptive backoff
    # prevents thundering herd. This fires ONLY for technical declines.
    monitor = get_monitor()
    issuer_health = monitor.get_health(event.issuer_bank)
    if issuer_health == IssuerHealthLevel.CRITICAL:
        td_rate = monitor.get_td_rate(event.issuer_bank)
        backoff = monitor.get_backoff_minutes(event.issuer_bank)
        hits.append(ConstraintHit(
            code=ConstraintCode.ISSUER_SYSTEMIC_BACKOFF,
            message=f"Issuer {event.issuer_bank.value} showing {td_rate:.1%} technical decline rate in current batch — adaptive backoff prevents thundering herd; retry in {backoff:.0f}min",
            forced_action=Action.DELAYED_RETRY,
            min_delay_minutes=backoff,
        ))
        # Don't return here — allow subsequent checks (issuer backoff is not a hard stop)

    # ── 13. Mandate vitality critical ────────────────────────────────────────
    vitality_level, vitality_score, vitality_reasons = score_mandate_vitality(event)
    if vitality_level == MandateVitalityLevel.LIKELY_DEAD:
        hits.append(ConstraintHit(
            code=ConstraintCode.MANDATE_VITALITY_CRITICAL,
            message=f"Mandate health critical (score={vitality_score:.1f}/10: {', '.join(vitality_reasons[:2])}) — proactive dunning is better than wasting final retry attempt",
            forced_action=Action.DUNNING,
        ))
        return hits

    # Netbanking: no smart retry (no mandate mechanism for re-debit)
    if event.rail == Rail.NETBANKING:
        hits.append(ConstraintHit(
            code=ConstraintCode.NETBANKING_NO_RETRY,
            message="Netbanking has no mandate-based re-debit mechanism — payment link dunning only",
            forced_action=Action.DUNNING,
        ))
        return hits

    return hits
