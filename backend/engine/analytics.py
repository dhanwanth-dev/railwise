"""
Batch analytics: multi-seed stability and ablation comparisons.
"""

from __future__ import annotations

import statistics
from typing import Any

from data.generator import generate_batch
from engine.config import EngineConfig
from engine.pipeline import run_batch


def run_stability(
    *,
    n_seeds: int = 30,
    batch_size: int = 500,
    seed_start: int = 1,
) -> dict[str, Any]:
    """Run Railwise vs baseline across multiple seeds; return aggregate stats."""
    rows: list[dict[str, Any]] = []

    for i in range(n_seeds):
        seed = seed_start + i
        events = generate_batch(batch_size, seed=seed)
        _, _, _, rw = run_batch(events, policy="railwise")
        _, _, _, bl = run_batch(events, policy="baseline_static")

        rows.append({
            "seed": seed,
            "railwise_soft_recovery": round(rw.soft_recovery_rate, 4),
            "baseline_soft_recovery": round(bl.soft_recovery_rate, 4),
            "soft_delta_pp": round((rw.soft_recovery_rate - bl.soft_recovery_rate) * 100, 2),
            "railwise_recovered_paise": rw.recovered_paise,
            "baseline_recovered_paise": bl.recovered_paise,
            "recovered_delta_paise": rw.recovered_paise - bl.recovered_paise,
            "railwise_hard_wasted": rw.hard_decline_wasted_retries,
            "baseline_hard_wasted": bl.hard_decline_wasted_retries,
            "railwise_upi_violations": rw.upi_cooldown_violations,
            "baseline_upi_violations": bl.upi_cooldown_violations,
            "railwise_wins_recovery": rw.soft_recovery_rate > bl.soft_recovery_rate,
            "railwise_wins_paise": rw.recovered_paise > bl.recovered_paise,
        })

    soft_deltas = [r["soft_delta_pp"] for r in rows]
    paise_deltas = [r["recovered_delta_paise"] for r in rows]
    rw_soft = [r["railwise_soft_recovery"] for r in rows]

    return {
        "n_seeds": n_seeds,
        "batch_size": batch_size,
        "seeds": rows,
        "summary": {
            "railwise_wins_soft_rate": sum(1 for r in rows if r["railwise_wins_recovery"]),
            "railwise_wins_paise": sum(1 for r in rows if r["railwise_wins_paise"]),
            "avg_soft_delta_pp": round(statistics.mean(soft_deltas), 2),
            "std_soft_delta_pp": round(statistics.pstdev(soft_deltas) if len(soft_deltas) > 1 else 0, 2),
            "min_soft_delta_pp": round(min(soft_deltas), 2),
            "max_soft_delta_pp": round(max(soft_deltas), 2),
            "avg_recovered_delta_paise": round(statistics.mean(paise_deltas)),
            "railwise_soft_recovery_mean": round(statistics.mean(rw_soft), 4),
            "railwise_soft_recovery_std": round(statistics.pstdev(rw_soft) if len(rw_soft) > 1 else 0, 4),
            "zero_hard_wasted_all_seeds": all(r["railwise_hard_wasted"] == 0 for r in rows),
            "zero_upi_violations_all_seeds": all(r["railwise_upi_violations"] == 0 for r in rows),
            "baseline_has_violations_all_seeds": all(
                r["baseline_hard_wasted"] > 0 or r["baseline_upi_violations"] > 0 for r in rows
            ),
        },
    }


def run_ablation_batch(
    *,
    batch_size: int = 200,
    seed: int = 42,
) -> dict[str, Any]:
    """Compare full Railwise vs ablated configs on the same batch."""
    events = generate_batch(batch_size, seed=seed)
    variants: list[tuple[str, EngineConfig]] = [
        ("full_railwise", EngineConfig.full()),
        ("no_ml_model", EngineConfig(use_ml_model=False)),
        ("no_compliance", EngineConfig(use_compliance_blocks=False)),
        ("no_issuer_health", EngineConfig(use_issuer_health=False)),
        ("no_mandate_vitality", EngineConfig(use_mandate_vitality=False)),
        ("rules_only", EngineConfig.rules_only()),
    ]

    results: list[dict[str, Any]] = []
    for label, cfg in variants:
        _, _, audits, metrics = run_batch(events, policy="railwise", config=cfg)
        results.append({
            "variant": label,
            "config": cfg.model_dump(),
            "config_label": cfg.label(),
            "metrics": metrics.model_dump(),
            "sample_audits": audits[:5],
        })

    full = results[0]["metrics"]
    comparisons = []
    for r in results[1:]:
        m = r["metrics"]
        comparisons.append({
            "variant": r["variant"],
            "vs_full": {
                "soft_recovery_delta_pp": round((m["soft_recovery_rate"] - full["soft_recovery_rate"]) * 100, 2),
                "recovered_paise_delta": m["recovered_paise"] - full["recovered_paise"],
                "hard_wasted_delta": m["hard_decline_wasted_retries"] - full["hard_decline_wasted_retries"],
                "upi_violations_delta": m["upi_cooldown_violations"] - full["upi_cooldown_violations"],
                "pdn_blocks_delta": (m.get("pdn_compliance_blocks") or 0) - (full.get("pdn_compliance_blocks") or 0),
                "issuer_backoffs_delta": (m.get("issuer_adaptive_backoffs") or 0) - (full.get("issuer_adaptive_backoffs") or 0),
            },
        })

    return {
        "seed": seed,
        "batch_size": batch_size,
        "variants": results,
        "comparisons": comparisons,
    }


def run_model_training_stability(*, n_seeds: int = 5) -> dict[str, Any]:
    """Train model with multiple seeds; show metric stability."""
    from data.train_model import run_training

    rows = []
    for i in range(n_seeds):
        seed = 10 + i * 7
        result = run_training(train_seed=seed)
        m = result["metrics"]
        rows.append({
            "train_seed": seed,
            "accuracy": m["accuracy"],
            "soft_recall": m["soft_recall"],
            "hard_recall": m["hard_recall"],
            "avg_recov_soft": m["avg_recov_soft"],
            "avg_recov_hard": m["avg_recov_hard"],
            "quality_passed": result["quality_passed"],
        })

    acc = [r["accuracy"] for r in rows]
    soft = [r["soft_recall"] for r in rows]
    return {
        "n_seeds": n_seeds,
        "runs": rows,
        "summary": {
            "accuracy_mean": round(statistics.mean(acc), 4),
            "accuracy_std": round(statistics.pstdev(acc) if len(acc) > 1 else 0, 4),
            "accuracy_min": round(min(acc), 4),
            "accuracy_max": round(max(acc), 4),
            "soft_recall_mean": round(statistics.mean(soft), 4),
            "soft_recall_std": round(statistics.pstdev(soft) if len(soft) > 1 else 0, 4),
            "all_passed_quality": all(r["quality_passed"] for r in rows),
        },
    }


def compare_single_event(event: dict, *, kill_switch: bool = False) -> dict[str, Any]:
    """Run one event through full + ablated configs for live sandbox."""
    variants: list[tuple[str, EngineConfig]] = [
        ("full_railwise", EngineConfig.full()),
        ("no_ml_model", EngineConfig(use_ml_model=False)),
        ("no_compliance", EngineConfig(use_compliance_blocks=False)),
        ("no_issuer_health", EngineConfig(use_issuer_health=False)),
        ("no_mandate_vitality", EngineConfig(use_mandate_vitality=False)),
        ("rules_only", EngineConfig.rules_only()),
    ]

    from engine.pipeline import decide

    decisions = []
    for label, cfg in variants:
        d = decide(event, policy="railwise", kill_switch=kill_switch, simulate=False, config=cfg)
        decisions.append({
            "variant": label,
            "config_label": cfg.label(),
            "action": d.action.value,
            "recoverability": d.classification.recoverability,
            "decline_kind": d.classification.decline_kind.value,
            "classification_source": d.classification.source,
            "constraint_codes": [h.code.value for h in d.constraint_hits],
            "reason_chain": d.reason_chain,
            "delay_minutes": d.delay_minutes,
            "feature_importance": d.classification.feature_importance,
        })

    full_action = decisions[0]["action"]
    diffs = [
        {"variant": d["variant"], "action_changed": d["action"] != full_action, "action": d["action"]}
        for d in decisions[1:]
    ]

    return {"decisions": decisions, "diffs_from_full": diffs}
