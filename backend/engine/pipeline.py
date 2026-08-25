"""Public pipeline entrypoints: decide + run_batch."""

from __future__ import annotations

from typing import Optional

from engine.audit import compute_metrics, decision_to_audit_record
from engine.baseline import decide_baseline
from engine.execute import execute
from engine.normalize import normalize_raw
from engine.policy import decide_railwise
from engine.schemas import BatchMetrics, Decision, PaymentFailureEvent


def decide(
    event: PaymentFailureEvent | dict,
    *,
    policy: str = "railwise",
    kill_switch: bool = False,
    prior_decision: Optional[Decision] = None,
    simulate: bool = True,
) -> Decision:
    if isinstance(event, dict):
        event = normalize_raw(event)

    if policy == "baseline_static":
        decision = decide_baseline(event)
    else:
        decision = decide_railwise(event, kill_switch=kill_switch, prior_decision=prior_decision)

    if simulate:
        decision = execute(event, decision)
    return decision


def run_batch(
    events: list[PaymentFailureEvent | dict],
    *,
    policy: str = "railwise",
    kill_switch: bool = False,
) -> tuple[list[PaymentFailureEvent], list[Decision], list[dict], BatchMetrics]:
    normalized: list[PaymentFailureEvent] = []
    decisions: list[Decision] = []
    audits: list[dict] = []
    seen: dict[str, Decision] = {}

    for raw in events:
        event = normalize_raw(raw) if isinstance(raw, dict) else raw
        normalized.append(event)
        prior = None
        if policy == "railwise":
            from engine.policy import _idempotency_key

            key = _idempotency_key(event, "railwise")
            prior = seen.get(key)

        decision = decide(event, policy=policy, kill_switch=kill_switch, prior_decision=prior)
        if policy == "railwise":
            seen[decision.idempotency_key] = decision
        decisions.append(decision)
        audits.append(decision_to_audit_record(event, decision))

    metrics = compute_metrics(normalized, decisions, policy)
    return normalized, decisions, audits, metrics
