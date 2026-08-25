"""Static hourly retry baseline — deliberately naive for A/B comparison."""

from __future__ import annotations

import uuid
from datetime import timedelta

from engine.classify import classify
from engine.schemas import Action, Decision, DeclineKind, PaymentFailureEvent


def decide_baseline(event: PaymentFailureEvent) -> Decision:
    """
    Naive policy: retry every hour up to 5 times regardless of rail/decline kind.
    This creates measurable compliance violations and wasted hard-decline retries.
    """
    classification = classify(event)
    idem = f"baseline:{event.payment_id}:{event.attempt_number}"

    # Baseline ignores hard declines half-heartedly — still retries if attempt < 5
    if event.attempt_number >= 5:
        action = Action.STOP
        reason = ["baseline_max_attempts"]
        scheduled = None
        delay = None
    else:
        action = Action.DELAYED_RETRY
        scheduled = event.timestamp + timedelta(hours=1)
        delay = 60.0
        reason = [
            "baseline_static_hourly",
            f"rail_ignored={event.rail.value}",
            f"decline_kind_ignored={classification.decline_kind.value}",
        ]

    # Flag compliance issues for metrics (baseline still "decides" the bad action)
    compliance = False
    if classification.decline_kind == DeclineKind.HARD and action in (Action.DELAYED_RETRY, Action.RETRY_NOW):
        compliance = True
        reason.append("VIOLATION_hard_decline_retry")
    if event.rail.value == "upi" and event.attempt_number > 1 and event.hours_since_last_attempt * 60 < 20:
        if action in (Action.DELAYED_RETRY, Action.RETRY_NOW) and (delay or 60) < 20:
            compliance = True
            reason.append("VIOLATION_upi_cooldown")
        # hourly retry from attempt>1 with hours_since < 20 is always a cooldown violation intent
        if event.hours_since_last_attempt * 60 < 20:
            compliance = True
            if "VIOLATION_upi_cooldown" not in reason:
                reason.append("VIOLATION_upi_cooldown")

    return Decision(
        decision_id=str(uuid.uuid4()),
        payment_id=event.payment_id,
        action=action,
        scheduled_at=scheduled,
        delay_minutes=delay,
        classification=classification,
        constraint_hits=[],
        reason_chain=reason,
        idempotency_key=idem,
        policy_name="baseline_static",
        compliance_violation=compliance,
    )
