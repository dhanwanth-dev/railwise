"""Named edge-case fixtures — USP of the submission."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _base(**kwargs: Any) -> dict[str, Any]:
    payload = {
        "id": "pay_edge_default",
        "amount": 99900,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "customer_id": "cust_edge",
        "created_at": datetime(2026, 8, 20, 10, 0, 0).isoformat(),
        "attempt_number": 1,
        "hours_since_last_attempt": 0.0,
        "prior_soft_recoveries": 0,
        "prior_hard_declines": 0,
        "has_alt_upi_mandate": False,
        "has_alt_card": False,
        "mandate_revoked": False,
        "payday_day_of_month": 1,
        "error": {"code": "insufficient_funds", "reason": "insufficient_funds"},
    }
    payload.update(kwargs)
    if "error" in kwargs and isinstance(kwargs["error"], str):
        payload["error"] = {"code": kwargs["error"], "reason": kwargs["error"]}
    return payload


EDGE_CASES: dict[str, dict[str, Any]] = {
    "hard_decline_stop": {
        "title": "Hard decline → stop, zero retries",
        "fixture": _base(id="pay_hard_1", error={"code": "stolen_card", "reason": "stolen_card"}),
        "expected_action": "stop",
        "notes": "Scheme rules: never retry hard declines.",
    },
    "card_nsf_payday": {
        "title": "Soft NSF on card → delayed retry in payday-like window",
        "fixture": _base(
            id="pay_card_nsf",
            method="card",
            error={"code": "insufficient_funds", "reason": "insufficient_funds"},
            payday_day_of_month=1,
            attempt_number=1,
        ),
        "expected_action": "delayed_retry",
        "notes": "Timing biased toward synthetic payday.",
    },
    "upi_nsf_cooldown": {
        "title": "Soft NSF on UPI → cooldown then non-peak retry",
        "fixture": _base(
            id="pay_upi_nsf",
            method="upi",
            mandate_id="mandate_nsf",
            error={"code": "insufficient_funds", "reason": "insufficient_funds"},
            attempt_number=1,
            hours_since_last_attempt=0.0,
        ),
        "expected_action": "delayed_retry",
        "notes": "UPI preferred non-peak slot after legal delay.",
    },
    "upi_immediate_represent_blocked": {
        "title": "Immediate UPI re-present → cooldown constraint",
        "fixture": _base(
            id="pay_upi_cool",
            method="upi",
            mandate_id="mandate_cool",
            error={"code": "temporary_failure", "reason": "temporary_failure"},
            attempt_number=2,
            hours_since_last_attempt=0.1,  # 6 minutes
        ),
        "expected_action": "delayed_retry",
        "expected_constraint": "upi_cooldown",
        "notes": "Prevents double-debit / reconciliation risk.",
    },
    "upi_budget_exhausted_rail_switch": {
        "title": "UPI budget exhausted + high recoverability → rail-switch",
        "fixture": _base(
            id="pay_upi_budget",
            method="upi",
            mandate_id="mandate_budget",
            error={"code": "insufficient_funds", "reason": "insufficient_funds"},
            attempt_number=4,
            hours_since_last_attempt=48.0,
            has_alt_card=True,
            prior_soft_recoveries=3,
        ),
        "expected_action": "rail_switch",
        "expected_constraint": "attempt_budget_exhausted",
        "notes": "Featured failure: compliance ceiling beats recoverability score.",
    },
    "card_over_retry_dunning": {
        "title": "Card attempt 4+ diminishing returns → dunning/stop path",
        "fixture": _base(
            id="pay_card_over",
            method="card",
            error={"code": "insufficient_funds", "reason": "insufficient_funds"},
            attempt_number=4,
            hours_since_last_attempt=24.0,
        ),
        "expected_action": "rail_switch",  # attempt >= CARD_MAX forces rail_switch/dunning via budget
        "expected_constraint": "attempt_budget_exhausted",
        "notes": "Excess-auth risk / diminishing returns.",
    },
    "ambiguous_with_soft_history": {
        "title": "Ambiguous do_not_honor + soft history → contextual soft",
        "fixture": _base(
            id="pay_amb_soft",
            error={"code": "do_not_honor", "reason": "do_not_honor"},
            prior_soft_recoveries=2,
            prior_hard_declines=0,
            hours_since_last_attempt=24.0,
        ),
        "expected_decline_kind_in": ["soft", "ambiguous"],
        "notes": "Model/rules use history — AI only here.",
    },
    "ambiguous_no_history_conservative": {
        "title": "Ambiguous do_not_honor without history → conservative",
        "fixture": _base(
            id="pay_amb_none",
            error={"code": "do_not_honor", "reason": "do_not_honor"},
            prior_soft_recoveries=0,
            prior_hard_declines=0,
        ),
        "expected_action_in": ["delayed_retry", "dunning"],
        "notes": "No history → do not aggressively retry.",
    },
    "mandate_revoked_dunning": {
        "title": "Mandate revoked mid-sequence → dunning/win-back only",
        "fixture": _base(
            id="pay_revoked",
            method="upi",
            mandate_revoked=True,
            error={"code": "insufficient_funds", "reason": "insufficient_funds"},
        ),
        "expected_action": "dunning",
        "expected_constraint": "mandate_revoked",
        "notes": "Stop debit retries; retention-style dunning log only.",
    },
    "regulatory_no_retry": {
        "title": "RBI/approval-required → no retry queue",
        "fixture": _base(
            id="pay_rbi",
            error={"code": "rbi_approval_required", "reason": "rbi_approval_required"},
        ),
        "expected_action": "dunning",
        "expected_constraint": "regulatory_block",
        "notes": "Timing cannot fix a regulatory prerequisite.",
    },
    "network_timeout_short_delay": {
        "title": "Network timeout → short delayed retry (not hard stop)",
        "fixture": _base(
            id="pay_timeout",
            method="card",
            error={"code": "network_timeout", "reason": "network_timeout"},
            attempt_number=1,
            hours_since_last_attempt=0,
        ),
        "expected_action": "delayed_retry",
        "notes": "Transient processor error — recoverable.",
    },
    "dead_card_alt_upi_switch": {
        "title": "Weak card recoverability + active UPI → rail-switch",
        "fixture": _base(
            id="pay_dead_card",
            method="card",
            error={"code": "do_not_honor", "reason": "do_not_honor"},
            attempt_number=2,
            prior_soft_recoveries=0,
            prior_hard_declines=1,
            has_alt_upi_mandate=True,
            hours_since_last_attempt=12.0,
        ),
        "expected_action_in": ["rail_switch", "dunning", "delayed_retry"],
        "notes": "Blended customer: prefer alternate live rail when card looks weak.",
    },
    "amount_needs_customer_action": {
        "title": "Amount above Autopay comfort → dunning, not blind retry",
        "fixture": _base(
            id="pay_big_upi",
            method="upi",
            amount=20_000_00,
            error={"code": "insufficient_funds", "reason": "insufficient_funds"},
            attempt_number=1,
        ),
        "expected_action": "dunning",
        "expected_constraint": "amount_needs_customer_action",
        "notes": "AFA / customer action threshold.",
    },
}


def all_fixtures() -> list[tuple[str, dict[str, Any]]]:
    return [(k, v) for k, v in EDGE_CASES.items()]
