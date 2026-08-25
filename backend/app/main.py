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
from engine.pipeline import decide, run_batch  # noqa: E402
from engine.schemas import Action  # noqa: E402

app = FastAPI(
    title="Railwise",
    description="Constraint-first, rail-aware revenue recovery for UPI AutoPay + cards",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory latest batch for demo (also persisted)
_LATEST: dict[str, Any] = {}
_KILL_SWITCH = False


class DecideRequest(BaseModel):
    event: dict[str, Any]
    policy: str = "railwise"
    simulate: bool = True


class BatchRequest(BaseModel):
    n: int = Field(default=500, ge=50, le=5000)
    seed: int = 42
    persist: bool = True


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Ensure synthetic batch + model exist
    batch_path = ROOT / "data" / "synthetic_batch.json"
    if not batch_path.exists():
        write_batch(batch_path, 500, 42)
    model_path = ROOT / "data" / "models" / "ambiguous_clf.json"
    if not model_path.exists():
        from data.train_model import main as train_main

        train_main()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "railwise", "kill_switch": _KILL_SWITCH}


@app.post("/kill-switch")
def set_kill_switch(enabled: bool = True) -> dict:
    global _KILL_SWITCH
    _KILL_SWITCH = enabled
    return {"kill_switch": _KILL_SWITCH}


@app.post("/decide")
def api_decide(body: DecideRequest) -> dict:
    decision = decide(body.event, policy=body.policy, kill_switch=_KILL_SWITCH, simulate=body.simulate)
    return decision.model_dump(mode="json")


@app.post("/batch/run")
def api_batch_run(body: BatchRequest) -> dict:
    events = generate_batch(body.n, body.seed)
    _, rw_decisions, rw_audits, rw_metrics = run_batch(events, policy="railwise", kill_switch=_KILL_SWITCH)
    _, bl_decisions, bl_audits, bl_metrics = run_batch(events, policy="baseline_static", kill_switch=False)

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
        "lift": {
            "soft_recovery_rate_delta": round(
                rw_metrics.soft_recovery_rate - bl_metrics.soft_recovery_rate, 4
            ),
            "recovered_paise_delta": rw_metrics.recovered_paise - bl_metrics.recovered_paise,
            "hard_wasted_delta": rw_metrics.hard_decline_wasted_retries
            - bl_metrics.hard_decline_wasted_retries,
            "upi_violations_delta": rw_metrics.upi_cooldown_violations - bl_metrics.upi_cooldown_violations,
        },
    }
    global _LATEST
    _LATEST = result
    return result


@app.get("/batch/latest")
def api_batch_latest() -> dict:
    if not _LATEST:
        # auto-run once for demo convenience
        return api_batch_run(BatchRequest(n=500, seed=42))
    return _LATEST


@app.get("/audits")
def api_audits(
    policy: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    if not _LATEST:
        api_batch_run(BatchRequest(n=500, seed=42))
    audits = list(_LATEST.get("sample_audits") or [])
    # Prefer DB if available for fuller list
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
        results.append(
            {
                "id": key,
                "title": meta["title"],
                "notes": meta.get("notes"),
                "expected_action": meta.get("expected_action"),
                "expected_constraint": meta.get("expected_constraint"),
                "fixture": meta["fixture"],
                "decision": decision.model_dump(mode="json"),
            }
        )
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
