"""
Hard Constraint Gate — NEVER ML.

Priority order (non-negotiable):
1. Kill switch / hard decline / mandate revoked / regulatory → STOP or dunning-only
2. Attempt budget exhausted → RAIL_SWITCH (never another debit)
3. UPI reconciliation cooldown → DELAY (never immediate retry)
4. Card over-retry risk → STOP/DUNNING after attempt 3
5. Only then may policy/timing choose WHEN inside the legal window
"""

from __future__ import annotations

from engine.schemas import (
    Action,
    ClassificationResult,
    ConstraintCode,
    ConstraintHit,
    DeclineKind,
    PaymentFailureEvent,
    Rail,
)

# UPI: 1 original + 3 retries (NPCI-aligned demo rule)
UPI_MAX_ATTEMPTS = 4  # attempt_number includes original
CARD_MAX_SMART_ATTEMPTS = 3
UPI_MIN_COOLDOWN_MINUTES = 20.0  # mid of 15–30 min reconciliation window
# Amounts above this demo threshold need customer action (AFA / intent), not blind Autopay retry
UPI_AUTOPAY_COMFORT_PAISE = 15_000_00  # ₹15,000 in paise


def evaluate_constraints(
    event: PaymentFailureEvent,
    classification: ClassificationResult,
    *,
    kill_switch: bool = False,
) -> list[ConstraintHit]:
    hits: list[ConstraintHit] = []

    if kill_switch:
        hits.append(
            ConstraintHit(
                code=ConstraintCode.KILL_SWITCH,
                message="STOP_ALL_RETRIES flag engaged — no retries permitted",
                forced_action=Action.STOP,
            )
        )
        return hits

    if event.mandate_revoked or event.decline_code in ("mandate_revoked", "mandate_cancelled", "token_cancelled"):
        hits.append(
            ConstraintHit(
                code=ConstraintCode.MANDATE_REVOKED,
                message="Mandate revoked mid-sequence — stop debit retries; dunning/win-back only",
                forced_action=Action.DUNNING,
            )
        )
        return hits

    if classification.decline_kind == DeclineKind.REGULATORY or event.decline_code in (
        "rbi_approval_required",
        "approval_required",
        "authentication_required",
    ):
        hits.append(
            ConstraintHit(
                code=ConstraintCode.REGULATORY_BLOCK,
                message="Regulatory/approval prerequisite unmet — retry timing cannot fix this",
                forced_action=Action.DUNNING,
            )
        )
        return hits

    if classification.decline_kind == DeclineKind.HARD:
        hits.append(
            ConstraintHit(
                code=ConstraintCode.HARD_DECLINE,
                message="Hard decline — scheme rules make retry deterministic: never",
                forced_action=Action.STOP,
            )
        )
        return hits

    if event.rail == Rail.NETBANKING:
        hits.append(
            ConstraintHit(
                code=ConstraintCode.NETBANKING_NO_RETRY,
                message="Netbanking has no Autopay-style retry rail in this engine — switch/dunning",
                forced_action=Action.RAIL_SWITCH if event.has_alt_upi_mandate or event.has_alt_card else Action.DUNNING,
            )
        )
        return hits

    if event.rail == Rail.UPI and event.amount_paise > UPI_AUTOPAY_COMFORT_PAISE:
        hits.append(
            ConstraintHit(
                code=ConstraintCode.AMOUNT_NEEDS_CUSTOMER_ACTION,
                message="Amount above Autopay comfort/AFA threshold — customer action required, not blind retry",
                forced_action=Action.DUNNING,
            )
        )
        return hits

    # Attempt budget (UPI stricter; card capped for excess-auth risk)
    max_attempts = UPI_MAX_ATTEMPTS if event.rail == Rail.UPI else CARD_MAX_SMART_ATTEMPTS
    if event.attempt_number >= max_attempts:
        alt = event.has_alt_upi_mandate or event.has_alt_card or event.rail == Rail.CARD
        hits.append(
            ConstraintHit(
                code=ConstraintCode.ATTEMPT_BUDGET_EXHAUSTED,
                message=(
                    f"Attempt budget exhausted (attempt {event.attempt_number}/{max_attempts}). "
                    "Recoverability score is overridden — rail-switch/payment link, never another debit."
                ),
                forced_action=Action.RAIL_SWITCH if alt or True else Action.DUNNING,
            )
        )
        return hits

    if event.rail == Rail.UPI:
        hours = event.hours_since_last_attempt
        if event.attempt_number > 1 and hours * 60 < UPI_MIN_COOLDOWN_MINUTES:
            remaining = UPI_MIN_COOLDOWN_MINUTES - (hours * 60)
            hits.append(
                ConstraintHit(
                    code=ConstraintCode.UPI_COOLDOWN,
                    message=(
                        f"UPI reconciliation cooldown: need ≥{UPI_MIN_COOLDOWN_MINUTES:.0f}m before re-present; "
                        f"{remaining:.1f}m remaining"
                    ),
                    forced_action=Action.DELAYED_RETRY,
                    min_delay_minutes=remaining,
                )
            )
            # Do not return alone if we also want policy timing — but cooldown forces delay
            return hits

    if event.rail == Rail.CARD and event.attempt_number > CARD_MAX_SMART_ATTEMPTS:
        hits.append(
            ConstraintHit(
                code=ConstraintCode.CARD_OVER_RETRY,
                message="Card diminishing-returns / excess-auth risk past attempt 3 — stop hammering",
                forced_action=Action.DUNNING,
            )
        )
        return hits

    return hits


def apply_forced_action(hits: list[ConstraintHit]) -> ConstraintHit | None:
    """First hit with forced_action wins — priority is encoded by evaluate_constraints order."""
    for hit in hits:
        if hit.forced_action is not None:
            return hit
    return None
