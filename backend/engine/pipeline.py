"""
Public pipeline entrypoints: decide + run_batch.

Key change: run_batch now maintains an IssuerHealthMonitor that accumulates
cross-customer issuer failure signal during the batch. This enables adaptive
backoff for systemic issuer failures (thundering herd defense).

Why this matters: When 40% of SBI events in the first 20 events fail with a
technical code, the monitor flags SBI as CRITICAL. The constraint gate then
applies adaptive backoff for ALL subsequent SBI events in the batch —
preventing the engine from hammering an already-struggling bank.
"""

from __future__ import annotations

from typing import Optional

from engine.audit import compute_metrics, decision_to_audit_record
from engine.baseline import decide_baseline
from engine.execute import execute
from engine.issuer_health import is_technical_decline, reset_monitor
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

    # Reset issuer health monitor at the start of each Railwise batch.
    # This gives us a fresh cross-customer signal per batch run.
    # Baseline doesn't use the monitor (it's deliberately dumb).
    monitor = reset_monitor() if policy == "railwise" else None

    for raw in events:
        event = normalize_raw(raw) if isinstance(raw, dict) else raw
        normalized.append(event)

        prior = None
        if policy == "railwise":
            from engine.policy import _idempotency_key
            key = _idempotency_key(event, "railwise")
            prior = seen.get(key)

        decision = decide(event, policy=policy, kill_switch=kill_switch, prior_decision=prior)

        # Record this event into the issuer health monitor so subsequent decisions
        # can see the accumulating cross-customer issuer signal.
        if monitor is not None:
            monitor.record(event.issuer_bank, event.decline_code)

        if policy == "railwise":
            seen[decision.idempotency_key] = decision

        decisions.append(decision)
        audits.append(decision_to_audit_record(event, decision))

    metrics = compute_metrics(normalized, decisions, policy)
    return normalized, decisions, audits, metrics
