"""
Execution adapter — bounded actions only. Demo: simulates outcomes, never sends real messages.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from engine.schemas import Action, Decision, DeclineKind, PaymentFailureEvent


def _recovery_probability(event: PaymentFailureEvent, decision: Decision) -> float:
    """Synthetic success model for batch metrics — not a production predictor."""
    if decision.action == Action.STOP:
        return 0.0
    if decision.action == Action.DUNNING:
        return 0.22  # customer-action recovery
    if decision.action == Action.RAIL_SWITCH:
        return 0.48
    if decision.classification.decline_kind == DeclineKind.HARD:
        return 0.0
    if decision.classification.decline_kind == DeclineKind.REGULATORY:
        return 0.05

    base = decision.classification.recoverability
    # Diminishing returns by attempt
    base *= max(0.2, 1.0 - 0.18 * (event.attempt_number - 1))
    if decision.action == Action.RETRY_NOW:
        base *= 0.9
    if decision.compliance_violation:
        base *= 0.5  # bad timing hurts
    # Railwise timing bonus vs baseline static
    if decision.policy_name == "railwise" and decision.action == Action.DELAYED_RETRY:
        base = min(0.92, base * 1.25)
    if decision.policy_name == "baseline_static":
        base *= 0.72
    return max(0.0, min(0.95, base))


def execute(event: PaymentFailureEvent, decision: Decision, *, seed: Optional[str] = None) -> Decision:
    """
    Simulate execution. Uses deterministic hash so same batch is reproducible.
    """
    out = decision.model_copy(deep=True)
    key = seed or f"{decision.policy_name}:{event.payment_id}:{event.attempt_number}:{decision.action.value}"
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    roll = (h % 10000) / 10000.0
    p = _recovery_probability(event, decision)

    if decision.action in (Action.RETRY_NOW, Action.DELAYED_RETRY, Action.RAIL_SWITCH, Action.DUNNING):
        if roll < p:
            out.executed = True
            out.execution_result = "recovered"
            out.recovered_amount_paise = event.amount_paise
        else:
            out.executed = True
            out.execution_result = "failed_again" if decision.action != Action.DUNNING else "dunning_no_response"
            out.recovered_amount_paise = 0
    else:
        out.executed = True
        out.execution_result = "stopped"
        out.recovered_amount_paise = 0

    # Annotate what we would have done for rail-switch / dunning (demo logging)
    if decision.action == Action.RAIL_SWITCH:
        target = decision.target_rail.value if decision.target_rail else "payment_link"
        out.execution_result = f"{out.execution_result}|would_send_{target}_recovery"
    if decision.action == Action.DUNNING:
        out.execution_result = f"{out.execution_result}|would_send_dunning_message"

    return out
