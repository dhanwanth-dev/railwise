"""Immutable append-only decision audit helpers + batch metrics."""

from __future__ import annotations

from collections import Counter

from engine.schemas import Action, BatchMetrics, Decision, DeclineKind, PaymentFailureEvent, Rail
from engine.constraints import UPI_MIN_COOLDOWN_MINUTES


def decision_to_audit_record(event: PaymentFailureEvent, decision: Decision) -> dict:
    return {
        "decision_id": decision.decision_id,
        "payment_id": decision.payment_id,
        "customer_id": event.customer_id,
        "rail": event.rail.value,
        "decline_code": event.decline_code,
        "amount_paise": event.amount_paise,
        "attempt_number": event.attempt_number,
        "action": decision.action.value,
        "scheduled_at": decision.scheduled_at.isoformat() if decision.scheduled_at else None,
        "delay_minutes": decision.delay_minutes,
        "target_rail": decision.target_rail.value if decision.target_rail else None,
        "decline_kind": decision.classification.decline_kind.value,
        "recoverability": decision.classification.recoverability,
        "classification_source": decision.classification.source,
        "feature_importance": decision.classification.feature_importance,
        "constraint_hits": [h.model_dump() for h in decision.constraint_hits],
        "reason_chain": decision.reason_chain,
        "idempotency_key": decision.idempotency_key,
        "policy_name": decision.policy_name,
        "executed": decision.executed,
        "execution_result": decision.execution_result,
        "recovered_amount_paise": decision.recovered_amount_paise,
        "compliance_violation": decision.compliance_violation,
        "created_at": decision.created_at.isoformat(),
    }


def compute_metrics(
    events: list[PaymentFailureEvent],
    decisions: list[Decision],
    policy_name: str,
) -> BatchMetrics:
    soft = sum(1 for d in decisions if d.classification.decline_kind == DeclineKind.SOFT)
    hard = sum(1 for d in decisions if d.classification.decline_kind == DeclineKind.HARD)
    recovered = [d for d in decisions if d.recovered_amount_paise > 0]
    soft_recovered = sum(
        1
        for d in decisions
        if d.classification.decline_kind == DeclineKind.SOFT and d.recovered_amount_paise > 0
    )

    hard_wasted = 0
    upi_violations = 0
    for event, decision in zip(events, decisions):
        if decision.classification.decline_kind == DeclineKind.HARD and decision.action in (
            Action.RETRY_NOW,
            Action.DELAYED_RETRY,
        ):
            hard_wasted += 1
        if decision.compliance_violation and any("upi_cooldown" in r for r in decision.reason_chain):
            upi_violations += 1
        # Also detect Railwise mistakes (should be 0)
        if (
            event.rail == Rail.UPI
            and event.attempt_number > 1
            and event.hours_since_last_attempt * 60 < UPI_MIN_COOLDOWN_MINUTES
            and decision.action == Action.RETRY_NOW
        ):
            upi_violations += 1
        if (
            event.rail == Rail.UPI
            and event.attempt_number > 1
            and event.hours_since_last_attempt * 60 < UPI_MIN_COOLDOWN_MINUTES
            and decision.action == Action.DELAYED_RETRY
            and (decision.delay_minutes or 0) < UPI_MIN_COOLDOWN_MINUTES
        ):
            upi_violations += 1

    actions = Counter(d.action.value for d in decisions)
    audited = sum(1 for d in decisions if d.reason_chain)

    soft_rate = (soft_recovered / soft) if soft else 0.0
    return BatchMetrics(
        policy_name=policy_name,
        total_failures=len(decisions),
        soft_failures=soft,
        hard_failures=hard,
        recovered_count=len(recovered),
        recovered_paise=sum(d.recovered_amount_paise for d in recovered),
        soft_recovery_rate=round(soft_rate, 4),
        hard_decline_wasted_retries=hard_wasted,
        upi_cooldown_violations=upi_violations,
        decisions_with_audit=audited,
        audit_coverage_pct=round(100.0 * audited / len(decisions), 2) if decisions else 0.0,
        action_counts=dict(actions),
    )
