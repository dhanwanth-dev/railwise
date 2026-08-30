"""
Policy / decision engine — runs ONLY after the Hard Constraint Gate.

Chooses action + timing inside the allowed set. Uses mandate vitality and
issuer health as contextual signals (not hard constraints — those are in constraints.py).

UPI non-peak hours per NPCI OC/215A/2025-26:
  Non-peak = before 10:00 AM | 1:00 PM–5:00 PM | after 9:30 PM
  Peak = 10:00 AM–1:00 PM and 5:00 PM–9:30 PM
  AutoPay mandate execution is only allowed during non-peak hours.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Optional

from engine.classify import classify
from engine.constraints import (
    UPI_MIN_REPRESENT_GAP_MINUTES,
    apply_forced_action,
    evaluate_constraints,
)
from engine.mandate_vitality import get_recoverability_multiplier, score_mandate_vitality
from engine.schemas import (
    Action,
    ConstraintCode,
    Decision,
    DeclineKind,
    IssuerBank,
    MandateVitalityLevel,
    PaymentFailureEvent,
    Rail,
)

# Non-peak hours for UPI AutoPay (NPCI OC/215A/2025-26)
# Peak: 10:00–13:00 and 17:00–21:30
# Non-peak (integer hour representation):
UPI_PREFERRED_HOURS = list(range(0, 10)) + list(range(13, 17)) + list(range(22, 24))
# Hour 21 (9PM-9:59PM) straddles the peak/non-peak boundary at 9:30PM — conservatively excluded

# Issuer-specific timing hints (when does this bank have lower TD rates?)
# Based on NPCI monthly reports and general payment infrastructure patterns
ISSUER_AVOID_HOURS: dict[str, list[int]] = {
    "sbi": [8, 9, 10, 11, 12, 13],    # SBI peaks on morning hours (salary day rush)
    "bandhan": [10, 11, 12, 13, 14],   # Bandhan has microfinance concentration in late morning
    "jio": list(range(9, 18)),          # Jio Payments Bank has broad peak coverage
}


def _idempotency_key(event: PaymentFailureEvent, policy_name: str) -> str:
    raw = f"{policy_name}:{event.payment_id}:{event.attempt_number}:{event.decline_code}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _is_preferred_hour(hour: int, issuer: IssuerBank) -> bool:
    """Return True if this hour is a preferred retry hour for the given issuer."""
    if hour not in UPI_PREFERRED_HOURS:
        return False
    avoid = ISSUER_AVOID_HOURS.get(issuer.value, [])
    return hour not in avoid


def _next_upi_slot(from_dt: datetime, min_delay_minutes: float, issuer: IssuerBank) -> datetime:
    """Find the next non-peak UPI slot after the minimum delay, avoiding issuer-specific peaks."""
    candidate = from_dt + timedelta(minutes=max(min_delay_minutes, UPI_MIN_REPRESENT_GAP_MINUTES))
    for _ in range(72):  # search up to 3 days
        if _is_preferred_hour(candidate.hour, issuer):
            return candidate
        candidate += timedelta(hours=1)
    return from_dt + timedelta(minutes=min_delay_minutes)


def _payday_retry(event: PaymentFailureEvent, from_dt: datetime) -> datetime:
    """NSF → bias retry toward synthetic payday window."""
    if event.payday_day_of_month:
        target_day = int(event.payday_day_of_month)
        year, month = from_dt.year, from_dt.month
        try:
            target = datetime(year, month, min(target_day, 28), 10, 0, 0)
        except ValueError:
            target = from_dt + timedelta(days=2)
        if target <= from_dt:
            if month == 12:
                target = datetime(year + 1, 1, min(target_day, 28), 10, 0, 0)
            else:
                target = datetime(year, month + 1, min(target_day, 28), 10, 0, 0)
        # Clamp to 5 days max to keep within mandate window
        if (target - from_dt).total_seconds() > 5 * 86400:
            return from_dt + timedelta(days=2, hours=2)
        return target
    return from_dt + timedelta(hours=36)


def _rank_timing(event: PaymentFailureEvent, classification, min_delay: float) -> tuple[datetime, float, str]:
    """
    Rank timing slots among legally-allowed options.
    AI role: choose WHEN within the legal window (payday, non-peak, issuer-specific).
    Not AI role: choosing minimum delay (that's compliance in constraints.py).
    """
    now = event.timestamp
    code = event.decline_code

    if event.rail == Rail.UPI:
        base_delay = max(min_delay, UPI_MIN_REPRESENT_GAP_MINUTES)

        if "insufficient" in code or code in ("nsf", "51"):
            # NSF: retry after payday, in non-peak window, avoiding issuer-specific peaks
            payday_dt = _payday_retry(event, now)
            scheduled = _next_upi_slot(payday_dt, base_delay, event.issuer_bank)
            reason = f"upi_nsf_payday_nonpeak_slot|issuer={event.issuer_bank.value}"

        elif "timeout" in code or "technical" in code or "unavailable" in code or code in ("91", "96"):
            # Technical decline: bank issue is likely temporary. Retry sooner, in non-peak.
            scheduled = _next_upi_slot(now, max(base_delay, 45.0), event.issuer_bank)
            reason = f"upi_technical_nonpeak|issuer={event.issuer_bank.value}"

        elif "limit" in code or code in ("61", "65"):
            # Velocity limit: wait exactly 24h for daily limit reset
            scheduled = now + timedelta(minutes=1440)
            reason = "upi_velocity_24h_reset"

        else:
            scheduled = _next_upi_slot(now, base_delay, event.issuer_bank)
            reason = f"upi_default_nonpeak|issuer={event.issuer_bank.value}"

    else:  # CARD
        base_delay = max(min_delay, 0.0)

        if "insufficient" in code or code == "nsf" or code == "51":
            # NSF on card: retry near payday (3 days is Razorpay's observed sweet spot)
            payday_dt = _payday_retry(event, now)
            scheduled = payday_dt
            delay_h = max(base_delay / 60, 72)
            scheduled = max(scheduled, now + timedelta(hours=delay_h))
            reason = "card_nsf_payday_biased"

        elif "timeout" in code or "technical" in code or code in ("91", "96", "06"):
            # Technical: retry soon (30-90 minutes)
            delay_min = max(base_delay, 45.0)
            scheduled = now + timedelta(minutes=delay_min)
            reason = "card_technical_short_delay"

        else:
            # Default: retry in a few hours
            delay_min = max(base_delay, 240.0)
            scheduled = now + timedelta(minutes=delay_min)
            reason = "card_default_delay"

    delay_minutes = (scheduled - now).total_seconds() / 60.0
    return scheduled, round(delay_minutes, 1), reason


def decide_railwise(
    event: PaymentFailureEvent,
    *,
    kill_switch: bool = False,
    prior_decision: Optional[Decision] = None,
) -> Decision:
    classification = classify(event)
    idem_key = _idempotency_key(event, "railwise")
    decision_id = str(uuid.uuid4())

    # ── Idempotent replay ────────────────────────────────────────────────────
    if prior_decision is not None and prior_decision.idempotency_key == idem_key:
        replay = prior_decision.model_copy(deep=True)
        replay.decision_id = decision_id
        replay.reason_chain = list(prior_decision.reason_chain) + ["idempotent_replay"]
        return replay

    # ── Mandate vitality signal ──────────────────────────────────────────────
    vitality_level, vitality_score, _ = score_mandate_vitality(event)
    # Downweight recoverability for degraded mandates
    multiplier = get_recoverability_multiplier(vitality_level)
    if multiplier < 1.0:
        adjusted_recov = classification.recoverability * multiplier
        classification = classification.model_copy(update={
            "recoverability": round(adjusted_recov, 4),
            "reason_codes": list(classification.reason_codes) + [
                f"mandate_vitality={vitality_level.value}(x{multiplier})"
            ],
        })

    # ── Hard constraint gate ─────────────────────────────────────────────────
    constraint_hits = evaluate_constraints(event, classification, kill_switch=kill_switch)

    reason_chain: list[str] = [
        f"rail={event.rail.value}",
        f"issuer={event.issuer_bank.value}",
        f"decline={event.decline_code}",
        f"classify={classification.decline_kind.value}(recov={classification.recoverability:.2f})",
        f"mandate_vitality={vitality_level.value}",
    ]

    # Check for forced action from constraints
    forced: Optional[Action] = None
    min_delay: float = 0.0
    for hit in constraint_hits:
        reason_chain.append(f"constraint={hit.code.value}")
        if hit.forced_action is not None and forced is None:
            forced = hit.forced_action
        if hit.min_delay_minutes and hit.min_delay_minutes > min_delay:
            min_delay = hit.min_delay_minutes

    # ── Action selection ────────────────────────────────────────────────────
    if forced is not None:
        action = forced
        # Check if compliance beats recoverability
        for hit in constraint_hits:
            if hit.code in (
                ConstraintCode.ATTEMPT_BUDGET_EXHAUSTED,
                ConstraintCode.HARD_DECLINE,
                ConstraintCode.MANDATE_VITALITY_CRITICAL,
                ConstraintCode.ISSUER_SYSTEMIC_BACKOFF,
            ) and hit.overrides_recoverability:
                reason_chain.append("priority: compliance_over_recoverability")
                break
    elif classification.decline_kind == DeclineKind.HARD:
        action = Action.STOP
        reason_chain.append("hard_decline_no_constraint_needed_stop")
    elif classification.decline_kind == DeclineKind.REGULATORY:
        action = Action.DUNNING
        reason_chain.append("regulatory_dunning")
    elif classification.recoverability >= 0.55:
        action = Action.DELAYED_RETRY
        reason_chain.append(f"high_recoverability={classification.recoverability:.2f}_delayed_retry")
    elif classification.recoverability >= 0.30:
        # Mid-range: check if we have an alternative rail
        if event.has_alt_upi_mandate or event.has_alt_card:
            action = Action.RAIL_SWITCH
            reason_chain.append("mid_recoverability_alt_rail_available")
        else:
            action = Action.DELAYED_RETRY
            reason_chain.append(f"mid_recoverability={classification.recoverability:.2f}_no_alt_rail")
    else:
        # Low recoverability
        if vitality_level == MandateVitalityLevel.AT_RISK:
            action = Action.DUNNING
            reason_chain.append("low_recoverability_at_risk_mandate_dunning")
        elif event.has_alt_upi_mandate or event.has_alt_card:
            action = Action.RAIL_SWITCH
            reason_chain.append("low_recoverability_rail_switch")
        else:
            action = Action.DUNNING
            reason_chain.append(f"low_recoverability={classification.recoverability:.2f}_dunning")

    # ── Timing for retry/switch actions ─────────────────────────────────────
    scheduled_at = None
    delay_minutes = None
    target_rail = None

    if action in (Action.DELAYED_RETRY, Action.RETRY_NOW):
        if action == Action.RETRY_NOW:
            scheduled_at = event.timestamp
            delay_minutes = 0.0
            reason_chain.append("timing=immediate")
        else:
            scheduled_at, delay_minutes, timing_reason = _rank_timing(event, classification, min_delay)
            reason_chain.append(f"timing={timing_reason}")

    elif action == Action.RAIL_SWITCH:
        scheduled_at = event.timestamp + timedelta(minutes=30)
        delay_minutes = 30.0
        if event.rail == Rail.UPI and event.has_alt_card:
            target_rail = Rail.CARD
            reason_chain.append("rail_switch_target=card")
        elif event.rail == Rail.CARD and event.has_alt_upi_mandate:
            target_rail = Rail.UPI
            reason_chain.append("rail_switch_target=upi")
        else:
            reason_chain.append("rail_switch_target=payment_link")

    return Decision(
        decision_id=decision_id,
        payment_id=event.payment_id,
        action=action,
        scheduled_at=scheduled_at,
        delay_minutes=delay_minutes,
        target_rail=target_rail,
        classification=classification,
        constraint_hits=constraint_hits,
        reason_chain=reason_chain,
        idempotency_key=idem_key,
        policy_name="railwise",
        issuer_health_level=_get_issuer_health_label(event),
        mandate_vitality_level=vitality_level.value,
    )


def _get_issuer_health_label(event: PaymentFailureEvent) -> str:
    from engine.issuer_health import get_monitor
    return get_monitor().get_health(event.issuer_bank).value
