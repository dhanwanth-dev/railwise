"""FastAPI application — Railwise decision cockpit API."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import append_audit, get_session, init_db, save_batch  # noqa: E402
from data.fixtures import EDGE_CASES  # noqa: E402
from data.generator import generate_batch, write_batch  # noqa: E402
from engine.analytics import (
    compare_single_event,
    run_ablation_batch,
    run_model_training_stability,
    run_stability,
)
from engine.config import EngineConfig  # noqa: E402
from engine.issuer_health import get_monitor  # noqa: E402
from engine.mandate_vitality import score_mandate_vitality  # noqa: E402
from engine.pipeline import decide, run_batch  # noqa: E402
from engine.schemas import Action  # noqa: E402

app = FastAPI(
    title="Railwise",
    description="Constraint-first, rail-aware, issuer-intelligent revenue recovery for UPI AutoPay + cards",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_LATEST: dict[str, Any] = {}
_LATEST_STABILITY: dict[str, Any] = {}
_LATEST_TRAINING: dict[str, Any] = {}
_LATEST_ABLATION: dict[str, Any] = {}
_KILL_SWITCH = False


class JourneyRequest(BaseModel):
    rail: str = Field(default="upi", description="upi or card")
    scenario: str = Field(default="nsf_early_retry", description="nsf_early_retry | ambiguous_sbi | token_expired")
    use_ml_model: bool = True
    use_compliance_blocks: bool = True
    use_issuer_health: bool = True
    use_mandate_vitality: bool = True
    use_timing_ai: bool = True
    include_batch_feed: bool = True


def _journey_event(rail: str, scenario: str) -> dict[str, Any]:
    """Build a Razorpay-shaped failure for the Recovery Journey narrative."""
    rail = rail.lower().strip()
    method = "upi" if rail == "upi" else "card"
    base = {
        "id": f"pay_ForgeCLI_{method}_8xK2mQ",
        "amount": 99900,
        "currency": "INR",
        "status": "failed",
        "method": method,
        "customer_id": "cust_forgecli_arjun",
        "issuer_bank": "sbi",
        "created_at": "2026-09-01T10:15:00",
        "attempt_number": 2,
        "hours_since_last_attempt": 0.15,
        "prior_soft_recoveries": 1,
        "prior_hard_declines": 0,
        "consecutive_failures": 1,
        "last_successful_debit_days_ago": 30,
        "pre_debit_notification_sent": True,
        "has_alt_upi_mandate": method == "card",
        "has_alt_card": method == "upi",
        "mandate_revoked": False,
        "payday_day_of_month": 1,
        "mandate_id": f"mandate_{method}_sbi_4419",
        "token_id": "token_hdfc_coft_991" if method == "card" else None,
    }
    if scenario == "ambiguous_sbi":
        base.update({
            "error": {"code": "do_not_honor", "reason": "do_not_honor", "iso_code": "05"},
            "decline_iso_code": "05",
            "hours_since_last_attempt": 2.0,
            "attempt_number": 1,
        })
    elif scenario == "token_expired":
        base.update({
            "method": "card",
            "error": {"code": "token_expired", "reason": "token_expired"},
            "hours_since_last_attempt": 24.0,
            "attempt_number": 1,
        })
    else:
        # Default: NSF too soon after last attempt — UPI cooldown / card payday path
        base["error"] = {"code": "insufficient_funds", "reason": "insufficient_funds", "iso_code": "51"}
        base["decline_iso_code"] = "51"
    return base


@app.post("/journey/run")
def api_journey_run(body: JourneyRequest) -> dict:
    """
    Live Recovery Journey engine pass.
    Returns baseline + full Railwise + ablation config decisions, pipeline stages,
    and optional related failure feed from the latest batch.
    """
    event = _journey_event(body.rail, body.scenario)
    cfg = EngineConfig(
        use_ml_model=body.use_ml_model,
        use_compliance_blocks=body.use_compliance_blocks,
        use_issuer_health=body.use_issuer_health,
        use_mandate_vitality=body.use_mandate_vitality,
        use_timing_ai=body.use_timing_ai,
    )

    baseline = decide(event, policy="baseline_static", kill_switch=False, simulate=True)
    full = decide(event, policy="railwise", kill_switch=_KILL_SWITCH, simulate=True, config=EngineConfig.full())
    custom = decide(event, policy="railwise", kill_switch=_KILL_SWITCH, simulate=True, config=cfg)

    # Pipeline stages for narrative UI (derived from live decision, not hard-coded prose)
    stages = [
        {
            "id": "ingest",
            "title": "Ingest & normalize",
            "kind": "rules",
            "summary": f"{event['method'].upper()} · {event['issuer_bank'].upper()} · {event['error']['code']}",
            "fields": {
                "payment_id": event["id"],
                "rail": event["method"],
                "issuer": event["issuer_bank"],
                "attempt": event["attempt_number"],
                "hours_since_last": event["hours_since_last_attempt"],
                "iso": event.get("decline_iso_code") or event["error"].get("iso_code"),
            },
        },
        {
            "id": "classify",
            "title": "Classify decline",
            "kind": "ai" if full.classification.source == "model" else "rules",
            "summary": (
                f"{full.classification.decline_kind.value} · "
                f"recov {full.classification.recoverability:.2f} · "
                f"source {full.classification.source}"
            ),
            "fields": {
                "decline_kind": full.classification.decline_kind.value,
                "recoverability": full.classification.recoverability,
                "confidence": full.classification.confidence,
                "source": full.classification.source,
                "reason_codes": full.classification.reason_codes,
                "feature_importance": full.classification.feature_importance,
            },
        },
        {
            "id": "constraints",
            "title": "Constraint gate",
            "kind": "rules",
            "summary": (
                f"{len(full.constraint_hits)} hit(s)"
                if full.constraint_hits
                else "No forced constraint — policy may choose"
            ),
            "fields": {
                "hits": [
                    {"code": h.code.value, "message": h.message, "forced_action": h.forced_action.value if h.forced_action else None}
                    for h in full.constraint_hits
                ],
            },
        },
        {
            "id": "policy",
            "title": "Policy & timing",
            "kind": "policy",
            "summary": f"{full.action.value}" + (f" · delay {full.delay_minutes}m" if full.delay_minutes is not None else ""),
            "fields": {
                "action": full.action.value,
                "delay_minutes": full.delay_minutes,
                "target_rail": full.target_rail.value if full.target_rail else None,
                "issuer_health": full.issuer_health_level,
                "mandate_vitality": full.mandate_vitality_level,
                "reason_chain": full.reason_chain,
            },
        },
        {
            "id": "execute",
            "title": "Execution result",
            "kind": "execute",
            "summary": full.execution_result or "pending",
            "fields": {
                "railwise_result": full.execution_result,
                "railwise_recovered_paise": full.recovered_amount_paise,
                "baseline_result": baseline.execution_result,
                "baseline_recovered_paise": baseline.recovered_amount_paise,
            },
        },
    ]

    full_codes = [h.code.value for h in full.constraint_hits]
    custom_codes = [h.code.value for h in custom.constraint_hits]

    feed = []
    if body.include_batch_feed:
        if not _LATEST:
            # Warm a small batch so the journey has a live failure feed
            api_batch_run(BatchRequest(n=200, seed=2025, persist=False))
        for a in (_LATEST.get("sample_audits") or [])[:8]:
            feed.append(a)

    return {
        "event": event,
        "product": {
            "name": "ForgeCLI Pro",
            "amount_paise": 99900,
            "rail": body.rail,
            "scenario": body.scenario,
        },
        "baseline": baseline.model_dump(mode="json"),
        "full_railwise": full.model_dump(mode="json"),
        "your_config": custom.model_dump(mode="json"),
        "config": cfg.model_dump(),
        "action_changed": full.action != custom.action,
        "constraints_changed": full_codes != custom_codes,
        "diff_summary": {
            "full_action": full.action.value,
            "custom_action": custom.action.value,
            "full_constraints": full_codes,
            "custom_constraints": custom_codes,
            "full_delay_minutes": full.delay_minutes,
            "custom_delay_minutes": custom.delay_minutes,
            "full_recovered_paise": full.recovered_amount_paise,
            "custom_recovered_paise": custom.recovered_amount_paise,
        },
        "stages": stages,
        "failure_feed": feed,
        "batch_metrics": {
            "railwise": (_LATEST.get("railwise") if _LATEST else None),
            "baseline": (_LATEST.get("baseline") if _LATEST else None),
            "lift": (_LATEST.get("lift") if _LATEST else None),
        },
    }



class SandboxRequest(BaseModel):
    event: dict[str, Any]
    use_ml_model: bool = True
    use_compliance_blocks: bool = True
    use_issuer_health: bool = True
    use_mandate_vitality: bool = True
    use_timing_ai: bool = True
    compare_all_variants: bool = False


class StabilityRequest(BaseModel):
    n_seeds: int = Field(default=30, ge=5, le=50)
    batch_size: int = Field(default=500, ge=100, le=1000)
    seed_start: int = 1


class AblationRequest(BaseModel):
    batch_size: int = Field(default=200, ge=50, le=500)
    seed: int = 42


class TrainRequest(BaseModel):
    train_seed: int = 7
    n_train: int = Field(default=3000, ge=500, le=10000)
    n_test: int = Field(default=600, ge=100, le=2000)
    stability_runs: int = Field(default=0, ge=0, le=10)


class BatchRequest(BaseModel):
    n: int = Field(default=500, ge=50, le=5000)
    seed: int = 42
    persist: bool = True


class DecideRequest(BaseModel):
    event: dict[str, Any]
    policy: str = "railwise"
    simulate: bool = True
    use_ml_model: bool = True
    use_compliance_blocks: bool = True
    use_issuer_health: bool = True
    use_mandate_vitality: bool = True
    use_timing_ai: bool = True


@app.on_event("startup")
def _startup() -> None:
    init_db()
    batch_path = ROOT / "data" / "synthetic_batch.json"
    if not batch_path.exists():
        write_batch(batch_path, 500, 42)
    model_path = ROOT / "data" / "models" / "ambiguous_clf.json"
    if not model_path.exists():
        from data.train_model import main as train_main
        train_main()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "railwise", "version": "2.0.0", "kill_switch": _KILL_SWITCH}


@app.post("/kill-switch")
def set_kill_switch(enabled: bool = True) -> dict:
    global _KILL_SWITCH
    _KILL_SWITCH = enabled
    return {"kill_switch": _KILL_SWITCH}


@app.post("/decide")
def api_decide(body: DecideRequest) -> dict:
    cfg = EngineConfig(
        use_ml_model=body.use_ml_model,
        use_compliance_blocks=body.use_compliance_blocks,
        use_issuer_health=body.use_issuer_health,
        use_mandate_vitality=body.use_mandate_vitality,
        use_timing_ai=body.use_timing_ai,
    )
    decision = decide(
        body.event,
        policy=body.policy,
        kill_switch=_KILL_SWITCH,
        simulate=body.simulate,
        config=cfg if body.policy == "railwise" else None,
    )
    return decision.model_dump(mode="json")


@app.post("/sandbox/compare")
def api_sandbox_compare(body: SandboxRequest) -> dict:
    """Live ablation: compare full Railwise vs toggled variants on one event."""
    if body.compare_all_variants:
        return compare_single_event(body.event, kill_switch=_KILL_SWITCH)

    cfg = EngineConfig(
        use_ml_model=body.use_ml_model,
        use_compliance_blocks=body.use_compliance_blocks,
        use_issuer_health=body.use_issuer_health,
        use_mandate_vitality=body.use_mandate_vitality,
        use_timing_ai=body.use_timing_ai,
    )
    full = decide(body.event, policy="railwise", kill_switch=_KILL_SWITCH, simulate=False, config=EngineConfig.full())
    custom = decide(body.event, policy="railwise", kill_switch=_KILL_SWITCH, simulate=False, config=cfg)
    return {
        "full_railwise": full.model_dump(mode="json"),
        "your_config": custom.model_dump(mode="json"),
        "config": cfg.model_dump(),
        "action_changed": full.action != custom.action,
        "diffs": {
            "action": {"full": full.action.value, "custom": custom.action.value},
            "recoverability": {
                "full": full.classification.recoverability,
                "custom": custom.classification.recoverability,
            },
            "constraint_count": {
                "full": len(full.constraint_hits),
                "custom": len(custom.constraint_hits),
            },
        },
    }


@app.post("/analytics/stability")
def api_stability(body: StabilityRequest) -> dict:
    global _LATEST_STABILITY
    result = run_stability(
        n_seeds=body.n_seeds,
        batch_size=body.batch_size,
        seed_start=body.seed_start,
    )
    _LATEST_STABILITY = result
    return result


@app.get("/analytics/stability/latest")
def api_stability_latest() -> dict:
    if not _LATEST_STABILITY:
        return api_stability(StabilityRequest())
    return _LATEST_STABILITY


@app.post("/analytics/ablation")
def api_ablation(body: AblationRequest) -> dict:
    global _LATEST_ABLATION
    result = run_ablation_batch(batch_size=body.batch_size, seed=body.seed)
    _LATEST_ABLATION = result
    return result


@app.get("/analytics/ablation/latest")
def api_ablation_latest() -> dict:
    if not _LATEST_ABLATION:
        return api_ablation(AblationRequest())
    return _LATEST_ABLATION


def _build_training_payload(
    result: dict,
    *,
    train_seed: int = 7,
    n_train: int = 3000,
    n_test: int = 600,
    stability: dict | None = None,
) -> dict:
    """Wrap raw run_training() output with audit trail for Model Lab UI."""
    metrics = result.get("metrics") or {}
    audit_trail = [
        {"step": "generate_samples", "detail": f"{n_train} train + {n_test} test (seed={train_seed})"},
        {"step": "train_logistic_sgd", "detail": "60 epochs, L2=0.001, 15 features"},
        {"step": "evaluate_test_set", "detail": f"accuracy={metrics.get('accuracy', 0):.1%}"},
        {"step": "quality_gate", "detail": "PASSED" if result.get("quality_passed") else "FAILED"},
        {"step": "persist_weights", "detail": result.get("model_path", "data/models/ambiguous_clf.json")},
    ]
    return {
        **result,
        "audit_trail": audit_trail,
        "training_stability": stability,
    }


@app.post("/model/train")
def api_model_train(body: TrainRequest) -> dict:
    """Live model training — returns metrics audit trail."""
    from data.train_model import run_training
    from engine.classify import reload_model

    global _LATEST_TRAINING
    result = run_training(train_seed=body.train_seed, n_train=body.n_train, n_test=body.n_test)
    reload_model()

    stability = None
    if body.stability_runs > 0:
        stability = run_model_training_stability(n_seeds=body.stability_runs)

    payload = _build_training_payload(
        result,
        train_seed=body.train_seed,
        n_train=body.n_train,
        n_test=body.n_test,
        stability=stability,
    )
    _LATEST_TRAINING = payload
    return payload


@app.get("/model/training/latest")
def api_model_training_latest() -> dict:
    global _LATEST_TRAINING
    if not _LATEST_TRAINING or "metrics" not in _LATEST_TRAINING:
        from data.train_model import run_training
        from engine.classify import reload_model

        result = run_training()
        reload_model()
        _LATEST_TRAINING = _build_training_payload(result)
    return _LATEST_TRAINING


@app.get("/ai/usage")
def api_ai_usage() -> dict:
    """One-liner map of where AI is used (and where it is not)."""
    return {
        "layers": [
            {"name": "Ambiguous decline classifier", "uses_ai": True, "why": "Same code do_not_honor means different things on SBI vs HDFC — model learns issuer patterns."},
            {"name": "Recoverability score", "uses_ai": True, "why": "Tells policy how much to trust a retry — compliance still has final say."},
            {"name": "Timing (payday / non-peak)", "uses_ai": False, "why": "Rule-based slot ranking inside legal windows — interpretable, not a neural net."},
            {"name": "Issuer Health Monitor", "uses_ai": False, "why": "Sliding-window TD rate math — detects outages without ML."},
            {"name": "Mandate Vitality Scorer", "uses_ai": False, "why": "Weighted rules on failure history — proactive dunning before mandate dies."},
            {"name": "Hard constraint gate", "uses_ai": False, "why": "NPCI/RBI rules never get a model vote — compliance is absolute."},
            {"name": "Kill switch", "uses_ai": False, "why": "Human emergency override only."},
        ],
        "rule": "AI only votes when decline reason is ambiguous AND compliance already allows retry.",
        "real_data_note": "Real Razorpay PII cannot be used publicly. Training uses NPCI-calibrated synthetic data + ISO 8583 taxonomy.",
    }


@app.post("/batch/run")
def api_batch_run(body: BatchRequest) -> dict:
    events = generate_batch(body.n, body.seed)
    _, rw_decisions, rw_audits, rw_metrics = run_batch(events, policy="railwise", kill_switch=_KILL_SWITCH)
    _, bl_decisions, bl_audits, bl_metrics = run_batch(events, policy="baseline_static", kill_switch=False)

    # Capture issuer health summary after batch (monitor populated by run_batch)
    issuer_health_summary = get_monitor().summary()

    batch_id = str(uuid.uuid4())
    if body.persist:
        session = get_session()
        try:
            for rec in rw_audits + bl_audits:
                append_audit(session, rec, batch_id=batch_id)
            save_batch(session, batch_id, rw_metrics.model_dump(), bl_metrics.model_dump())
            session.commit()
        finally:
            session.close()

    result = {
        "batch_id": batch_id,
        "railwise": rw_metrics.model_dump(),
        "baseline": bl_metrics.model_dump(),
        "sample_audits": rw_audits[:25],
        "featured": _featured_from_audits(rw_audits),
        "issuer_health": issuer_health_summary,
        "lift": {
            "soft_recovery_rate_delta": round(
                rw_metrics.soft_recovery_rate - bl_metrics.soft_recovery_rate, 4
            ),
            "recovered_paise_delta": rw_metrics.recovered_paise - bl_metrics.recovered_paise,
            "hard_wasted_delta": rw_metrics.hard_decline_wasted_retries - bl_metrics.hard_decline_wasted_retries,
            "upi_violations_delta": rw_metrics.upi_cooldown_violations - bl_metrics.upi_cooldown_violations,
            "new_compliance_protections": {
                "pdn_blocks": rw_metrics.pdn_compliance_blocks,
                "token_dunnings": rw_metrics.token_dunnings,
                "mandate_vitality_dunnings": rw_metrics.mandate_vitality_dunnings,
                "issuer_adaptive_backoffs": rw_metrics.issuer_adaptive_backoffs,
                "customer_cancelled_stops": rw_metrics.customer_cancelled_stops,
            },
        },
    }
    global _LATEST
    _LATEST = result
    return result


@app.get("/batch/latest")
def api_batch_latest() -> dict:
    if not _LATEST:
        return api_batch_run(BatchRequest(n=500, seed=42))
    return _LATEST


@app.get("/audits")
def api_audits(
    policy: Optional[str] = None,
    action: Optional[str] = None,
    issuer: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    if not _LATEST:
        api_batch_run(BatchRequest(n=500, seed=42))
    audits = list(_LATEST.get("sample_audits") or [])
    session = get_session()
    try:
        from app.db import AuditRow
        q = session.query(AuditRow).order_by(AuditRow.id.desc())
        if _LATEST.get("batch_id"):
            q = q.filter(AuditRow.batch_id == _LATEST["batch_id"])
        if policy:
            q = q.filter(AuditRow.policy_name == policy)
        rows = q.limit(limit).all()
        audits = [json.loads(r.payload_json) for r in rows]
    finally:
        session.close()

    if action:
        audits = [a for a in audits if a.get("action") == action]
    if issuer:
        audits = [a for a in audits if a.get("issuer_bank") == issuer]
    return {"count": len(audits), "audits": audits[:limit]}


@app.get("/audits/{decision_id}")
def api_audit_one(decision_id: str) -> dict:
    session = get_session()
    try:
        from app.db import AuditRow
        row = session.query(AuditRow).filter(AuditRow.decision_id == decision_id).first()
        if not row:
            raise HTTPException(404, "Decision not found")
        return json.loads(row.payload_json)
    finally:
        session.close()


@app.get("/edge-cases")
def api_edge_cases() -> dict:
    results = []
    for key, meta in EDGE_CASES.items():
        decision = decide(meta["fixture"], policy="railwise", kill_switch=_KILL_SWITCH, simulate=True)
        results.append({
            "id": key,
            "title": meta["title"],
            "notes": meta.get("notes"),
            "expected_action": meta.get("expected_action"),
            "expected_constraint": meta.get("expected_constraint"),
            "fixture": meta["fixture"],
            "decision": decision.model_dump(mode="json"),
        })
    return {"edge_cases": results}


@app.get("/edge-cases/{case_id}")
def api_edge_case(case_id: str) -> dict:
    if case_id not in EDGE_CASES:
        raise HTTPException(404, "Unknown edge case")
    meta = EDGE_CASES[case_id]
    decision = decide(meta["fixture"], policy="railwise", kill_switch=_KILL_SWITCH, simulate=True)
    return {
        "id": case_id,
        "title": meta["title"],
        "notes": meta.get("notes"),
        "fixture": meta["fixture"],
        "decision": decision.model_dump(mode="json"),
        "featured": case_id == "upi_budget_exhausted_rail_switch",
    }


@app.get("/issuer-health")
def api_issuer_health() -> dict:
    """
    Cross-customer issuer health summary from the most recent batch run.
    Shows per-issuer technical decline rates, health levels, and adaptive backoff durations.
    Empty if no batch has run yet.
    """
    if not _LATEST:
        return {"message": "No batch run yet — run POST /batch/run first", "issuers": {}}
    return {
        "batch_id": _LATEST.get("batch_id"),
        "issuers": _LATEST.get("issuer_health", {}),
    }


@app.post("/mandate-vitality")
def api_mandate_vitality(body: dict) -> dict:
    """
    Score the vitality of a mandate based on its failure history.
    POST body: same shape as /decide event.
    Returns vitality level, raw score, and contributing factors.
    """
    from engine.normalize import normalize_raw
    event = normalize_raw(body)
    level, score, reasons = score_mandate_vitality(event)
    return {
        "payment_id": event.payment_id,
        "mandate_vitality_level": level.value,
        "vitality_score": score,
        "score_interpretation": {
            "0-2.5": "healthy — retry is worthwhile",
            "2.5-5": "at_risk — downweight recoverability",
            "5-10": "likely_dead — proactive dunning > wasted retry",
        }[
            "0-2.5" if score < 2.5 else ("2.5-5" if score < 5.0 else "5-10")
        ],
        "contributing_factors": reasons,
        "inputs": {
            "consecutive_failures": event.consecutive_failures,
            "last_successful_debit_days_ago": event.last_successful_debit_days_ago,
            "prior_hard_declines": event.prior_hard_declines,
            "prior_soft_recoveries": event.prior_soft_recoveries,
            "attempt_number": event.attempt_number,
        },
    }


@app.get("/actions")
def api_actions() -> dict:
    return {"actions": [a.value for a in Action]}


def _featured_from_audits(audits: list[dict]) -> Optional[dict]:
    for a in audits:
        hits = a.get("constraint_hits") or []
        if any(h.get("code") == "attempt_budget_exhausted" for h in hits) and a.get("action") == "rail_switch":
            return a
    for a in audits:
        if a.get("action") == "rail_switch":
            return a
    return audits[0] if audits else None
