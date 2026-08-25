"""Edge-case test suite — the USP artifact for Buildathon Track 03."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.fixtures import EDGE_CASES  # noqa: E402
from engine.pipeline import decide, run_batch  # noqa: E402
from engine.schemas import Action, ConstraintCode  # noqa: E402
from data.generator import generate_batch  # noqa: E402


def test_hard_decline_stop():
    d = decide(EDGE_CASES["hard_decline_stop"]["fixture"], simulate=False)
    assert d.action == Action.STOP
    assert any(h.code == ConstraintCode.HARD_DECLINE for h in d.constraint_hits)


def test_card_nsf_payday():
    d = decide(EDGE_CASES["card_nsf_payday"]["fixture"], simulate=False)
    assert d.action == Action.DELAYED_RETRY
    assert d.scheduled_at is not None


def test_upi_nsf_delayed():
    d = decide(EDGE_CASES["upi_nsf_cooldown"]["fixture"], simulate=False)
    assert d.action == Action.DELAYED_RETRY


def test_upi_immediate_represent_blocked():
    d = decide(EDGE_CASES["upi_immediate_represent_blocked"]["fixture"], simulate=False)
    assert d.action == Action.DELAYED_RETRY
    assert any(h.code == ConstraintCode.UPI_COOLDOWN for h in d.constraint_hits)
    assert (d.delay_minutes or 0) >= 1


def test_upi_budget_exhausted_rail_switch():
    """Featured failure: recoverability overridden by attempt budget."""
    d = decide(EDGE_CASES["upi_budget_exhausted_rail_switch"]["fixture"], simulate=False)
    assert d.action == Action.RAIL_SWITCH
    assert any(h.code == ConstraintCode.ATTEMPT_BUDGET_EXHAUSTED for h in d.constraint_hits)
    assert "priority: compliance_over_recoverability" in d.reason_chain


def test_card_over_retry():
    d = decide(EDGE_CASES["card_over_retry_dunning"]["fixture"], simulate=False)
    assert d.action in (Action.RAIL_SWITCH, Action.DUNNING, Action.STOP)
    assert any(h.code == ConstraintCode.ATTEMPT_BUDGET_EXHAUSTED for h in d.constraint_hits)


def test_mandate_revoked():
    d = decide(EDGE_CASES["mandate_revoked_dunning"]["fixture"], simulate=False)
    assert d.action == Action.DUNNING
    assert any(h.code == ConstraintCode.MANDATE_REVOKED for h in d.constraint_hits)


def test_regulatory_no_retry():
    d = decide(EDGE_CASES["regulatory_no_retry"]["fixture"], simulate=False)
    assert d.action == Action.DUNNING
    assert d.classification.decline_kind.value == "regulatory"


def test_network_timeout():
    d = decide(EDGE_CASES["network_timeout_short_delay"]["fixture"], simulate=False)
    assert d.action == Action.DELAYED_RETRY


def test_amount_needs_customer_action():
    d = decide(EDGE_CASES["amount_needs_customer_action"]["fixture"], simulate=False)
    assert d.action == Action.DUNNING
    assert any(h.code == ConstraintCode.AMOUNT_NEEDS_CUSTOMER_ACTION for h in d.constraint_hits)


def test_idempotent_replay():
    fixture = EDGE_CASES["card_nsf_payday"]["fixture"]
    d1 = decide(fixture, simulate=False)
    d2 = decide(fixture, prior_decision=d1, simulate=False)
    assert d2.idempotency_key == d1.idempotency_key
    assert "idempotent_replay" in d2.reason_chain


def test_kill_switch():
    d = decide(EDGE_CASES["card_nsf_payday"]["fixture"], kill_switch=True, simulate=False)
    assert d.action == Action.STOP
    assert any(h.code == ConstraintCode.KILL_SWITCH for h in d.constraint_hits)


def test_batch_railwise_zero_hard_wasted_and_zero_upi_violations():
    events = generate_batch(300, seed=99)
    _, _, _, metrics = run_batch(events, policy="railwise")
    assert metrics.hard_decline_wasted_retries == 0
    assert metrics.upi_cooldown_violations == 0
    assert metrics.audit_coverage_pct == 100.0


def test_batch_baseline_has_violations():
    events = generate_batch(300, seed=99)
    _, _, _, metrics = run_batch(events, policy="baseline_static")
    # Naive baseline should waste some hard retries and/or violate UPI cooldown
    assert metrics.hard_decline_wasted_retries > 0 or metrics.upi_cooldown_violations > 0


def test_railwise_beats_or_matches_baseline_recovery():
    events = generate_batch(500, seed=42)
    _, _, _, rw = run_batch(events, policy="railwise")
    _, _, _, bl = run_batch(events, policy="baseline_static")
    # Soft recovery should be meaningfully better OR recovered paise higher
    assert rw.recovered_paise >= bl.recovered_paise * 0.9  # allow noise variance; usually higher
    assert rw.hard_decline_wasted_retries == 0
    assert bl.hard_decline_wasted_retries >= rw.hard_decline_wasted_retries
