"""
Policy / decision engine — runs ONLY after Hard Constraint Gate.

Chooses action + timing inside the allowed set. Encodes diminishing returns.
AI timing ranker only ranks already-legal candidate slots.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Optional

from engine.classify import classify
from engine.constraints import UPI_MIN_COOLDOWN_MINUTES, apply_forced_action, evaluate_constraints
from engine.schemas import (
    Action,
    ConstraintCode,
    ConstraintHit,
    Decision,
    DeclineKind,
    PaymentFailureEvent,
    Rail,
)

# NPCI-aligned non-peak preference hours (demo): before 10, 13–17, after 21:30
UPI_PREFERRED_HOURS = list(range(0, 10)) + list(range(13, 17)) + list(range(22, 24))


def _idempotency_key(event: PaymentFailureEvent, policy_name: str) -> str:
    raw = f"{policy_name}:{event.payment_id}:{event.attempt_number}:{event.decline_code}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _next_upi_slot(from_dt: datetime, min_delay_minutes: float) -> datetime:
    candidate = from_dt + timedelta(minutes=max(min_delay_minutes, UPI_MIN_COOLDOWN_MINUTES))
    for _ in range(48):
        if candidate.hour in UPI_PREFERRED_HOURS:
            return candidate
        candidate += timedelta(hours=1)
    return from_dt + timedelta(minutes=min_delay_minutes)


def _payday_retry(event: PaymentFailureEvent, from_dt: datetime) -> datetime:
    """NSF → bias retry toward synthetic payday window (day-of-month hint)."""
    if event.payday_day_of_month:
        target_day = int(event.payday_day_of_month)
        year, month = from_dt.year, from_dt.month
        # If payday already passed this month, next month
        try:
            target = datetime(year, month, min(target_day, 28), 10, 0, 0)
        except ValueError:
            target = from_dt + timedelta(days=2)
        if target <= from_dt:
            if month == 12:
                target = datetime(year + 1, 1, min(target_day, 28), 10, 0, 0)
            else:
                target = datetime(year, month + 1, min(target_day, 28), 10, 0, 0)
        # Don't wait more than 5 days in demo batch simulation — clamp
        if (target - from_dt).total_seconds() > 5 * 86400:
            return from_dt + timedelta(days=2, hours=2)
        return target
    return from_dt + timedelta(hours=36)


def _rank_timing(event: PaymentFailureEvent, classification, min_delay: float) -> tuple[datetime, float, str]:
    """
    Timing ranker among legal slots only.
    Returns (scheduled_at, delay_minutes, reason).
    """
    now = event.timestamp
    code = event.decline_code

    if event.rail == Rail.UPI:
        base_delay = max(min_delay, UPI_MIN_COOLDOWN_MINUTES)
        if "insufficient" in code or code == "nsf":
            scheduled = _next_upi_slot(_payday_retry(event, now), base_delay)
            reason = "upi_nsf_payday_nonpeak_slot"
        elif "timeout" in code or "technical" in code:
            scheduled = _next_upi_slot(now, max(base_delay, 30))
            reason = "upi_transient_short_nonpeak"
        else:
            scheduled = _next_upi_slot(now, base_delay)
            reason = "upi_default_cooldown_nonpeak"
        delay = (scheduled - now).total_seconds() / 60.0
        return scheduled, delay, reason

    # Card
    if "insufficient" in code or code == "nsf":
        scheduled = _payday_retry(event, now)
        return scheduled, (scheduled - now).total_seconds() / 60.0, "card_nsf_payday_window"
    if "timeout" in code or "processing" in code:
        scheduled = now + timedelta(hours=2)
        return scheduled, 120.0, "card_transient_2h"
    # Ambiguous soft → conservative delay
    if classification.decline_kind == DeclineKind.AMBIGUOUS:
        scheduled = now + timedelta(hours=12)
        return scheduled, 720.0, "ambiguous_conservative_12h"
    scheduled = now + timedelta(hours=6)
    return scheduled, 360.0, "card_default_6h"


def decide_railwise(
    event: PaymentFailureEvent,
    *,
    kill_switch: bool = False,
    prior_decision: Optional[Decision] = None,
) -> Decision:
    """Full Railwise decision path with constraint priority + audit reason chain."""
    idem = _idempotency_key(event, "railwise")
    if prior_decision and prior_decision.idempotency_key == idem:
        # Idempotent replay — return prior decision annotated
        replay = prior_decision.model_copy(deep=True)
        replay.constraint_hits = list(replay.constraint_hits) + [
            ConstraintHit(
                code=ConstraintCode.IDEMPOTENT_REPLAY,
                message="Duplicate event for same payment_id+attempt — reusing prior decision",
                forced_action=replay.action,
                overrides_recoverability=True,
            )
        ]
        replay.reason_chain = list(replay.reason_chain) + ["idempotent_replay"]
        return replay

    classification = classify(event)
    reason_chain = [
        f"rail={event.rail.value}",
        f"decline_code={event.decline_code}",
        f"classified={classification.decline_kind.value}:{classification.source}",
        f"recoverability={classification.recoverability:.2f}",
    ]

    hits = evaluate_constraints(event, classification, kill_switch=kill_switch)
    forced = apply_forced_action(hits)

    if forced is not None:
        action = forced.forced_action
        assert action is not None
        reason_chain.append(f"constraint={forced.code.value}")
        reason_chain.append(f"forced_action={action.value}")
        reason_chain.append("priority: compliance_over_recoverability")

        scheduled = None
        delay = forced.min_delay_minutes
        target_rail = None

        if action == Action.DELAYED_RETRY:
            scheduled, delay, timing_reason = _rank_timing(event, classification, delay or UPI_MIN_COOLDOWN_MINUTES)
            reason_chain.append(timing_reason)
        elif action == Action.RAIL_SWITCH:
            if event.rail == Rail.CARD and event.has_alt_upi_mandate:
                target_rail = Rail.UPI
            elif event.rail == Rail.UPI and event.has_alt_card:
                target_rail = Rail.CARD
            elif event.has_alt_upi_mandate:
                target_rail = Rail.UPI
            else:
                target_rail = None  # payment link / intent
            reason_chain.append(f"rail_switch_target={target_rail.value if target_rail else 'payment_link'}")

        # Dead card + active UPI: prefer switch even on STOP if we somehow got here — handled in soft path

        return Decision(
            decision_id=str(uuid.uuid4()),
            payment_id=event.payment_id,
            action=action,
            scheduled_at=scheduled,
            delay_minutes=delay,
            target_rail=target_rail,
            classification=classification,
            constraint_hits=hits,
            reason_chain=reason_chain,
            idempotency_key=idem,
            policy_name="railwise",
        )

    # Soft path — policy selects among legal actions
    # Prefer rail-switch if card is dead-ish soft with alt UPI and low recoverability after attempt 2
    if (
        event.rail == Rail.CARD
        and event.has_alt_upi_mandate
        and event.attempt_number >= 2
        and classification.recoverability < 0.4
    ):
        reason_chain.append("soft_path_rail_switch_dead_card_alt_upi")
        return Decision(
            decision_id=str(uuid.uuid4()),
            payment_id=event.payment_id,
            action=Action.RAIL_SWITCH,
            target_rail=Rail.UPI,
            classification=classification,
            constraint_hits=hits,
            reason_chain=reason_chain,
            idempotency_key=idem,
            policy_name="railwise",
        )

    if classification.recoverability >= 0.55 and event.hours_since_last_attempt * 60 >= (
        UPI_MIN_COOLDOWN_MINUTES if event.rail == Rail.UPI else 0
    ):
        # High recoverability + legal window: delayed or now
        if event.rail == Rail.CARD and "timeout" in event.decline_code and event.hours_since_last_attempt >= 1:
            reason_chain.append("soft_path_retry_now_transient_cleared")
            return Decision(
                decision_id=str(uuid.uuid4()),
                payment_id=event.payment_id,
                action=Action.RETRY_NOW,
                classification=classification,
                constraint_hits=hits,
                reason_chain=reason_chain,
                idempotency_key=idem,
                policy_name="railwise",
            )

        scheduled, delay, timing_reason = _rank_timing(event, classification, 0)
        reason_chain.append(f"soft_path_delayed_retry:{timing_reason}")
        return Decision(
            decision_id=str(uuid.uuid4()),
            payment_id=event.payment_id,
            action=Action.DELAYED_RETRY,
            scheduled_at=scheduled,
            delay_minutes=delay,
            classification=classification,
            constraint_hits=hits,
            reason_chain=reason_chain,
            idempotency_key=idem,
            policy_name="railwise",
        )

    if classification.recoverability >= 0.35:
        scheduled, delay, timing_reason = _rank_timing(event, classification, 0)
        reason_chain.append(f"soft_path_moderate_recoverability:{timing_reason}")
        return Decision(
            decision_id=str(uuid.uuid4()),
            payment_id=event.payment_id,
            action=Action.DELAYED_RETRY,
            scheduled_at=scheduled,
            delay_minutes=delay,
            classification=classification,
            constraint_hits=hits,
            reason_chain=reason_chain,
            idempotency_key=idem,
            policy_name="railwise",
        )

    # Low recoverability → dunning rather than burning attempts
    reason_chain.append("soft_path_low_recoverability_dunning")
    return Decision(
        decision_id=str(uuid.uuid4()),
        payment_id=event.payment_id,
        action=Action.DUNNING,
        classification=classification,
        constraint_hits=hits,
        reason_chain=reason_chain,
        idempotency_key=idem,
        policy_name="railwise",
    )
