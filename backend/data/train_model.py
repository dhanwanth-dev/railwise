"""
Train ambiguous-code logistic classifier with issuer-bank features.

Training data is calibrated to real Indian payment failure patterns:
  - High-TD issuers (SBI, Bandhan, Jio) → ambiguous "do_not_honor" is more likely
    to be a technical soft decline, not a fraud hard decline
  - Low-TD issuers (HDFC, Axis) → "do_not_honor" is more likely fraud-engine reject
  - Recovery history strongly predicts recoverability
  - Consecutive failures are a strong negative signal

Model evaluation is printed at the end:
  - Train/test split accuracy
  - Soft recall (how many actual soft declines did we correctly classify)
  - Hard recall (how many actual hard declines did we correctly classify)
  - Average predicted recoverability for soft vs hard samples (calibration check)

Why logistic SGD (not scikit-learn, XGBoost, etc.):
  Buildathon context: auditable, zero binary deps, pure-Python.
  The weights are stored as human-readable JSON — a compliance investigator
  can read and verify what drove each decision.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.classify import FEATURE_NAMES, _sigmoid, train_ambiguous_model  # noqa: E402


def _make_sample(rng: random.Random) -> dict:
    """
    Generate one labeled training sample for the ambiguous-code classifier.
    Labels are derived from domain rules, calibrated to real issuer behavior.
    """
    is_upi = 1.0 if rng.random() < 0.52 else 0.0  # 52% UPI in India
    is_card = 1.0 - is_upi if is_upi == 1.0 else (1.0 if rng.random() < 0.9 else 0.0)

    # Issuer features — one-hot
    issuer_roll = rng.random()
    issuer_is_sbi = 1.0 if issuer_roll < 0.35 else 0.0      # SBI: 35% market share
    issuer_is_bandhan = 1.0 if 0.35 <= issuer_roll < 0.39 else 0.0
    issuer_is_jio = 1.0 if 0.39 <= issuer_roll < 0.41 else 0.0
    issuer_is_hdfc = 1.0 if 0.41 <= issuer_roll < 0.61 else 0.0  # HDFC: 20% share

    feats = {
        "is_upi": is_upi,
        "is_card": is_card,
        "attempt_number": float(rng.choices([1, 2, 3, 4], weights=[50, 27, 15, 8])[0]),
        "prior_soft_recoveries": float(rng.choices([0, 1, 2, 3, 4], weights=[40, 30, 18, 8, 4])[0]),
        "prior_hard_declines": float(rng.choices([0, 1, 2, 3], weights=[70, 20, 8, 2])[0]),
        "hours_since_last_attempt": rng.choices(
            [rng.uniform(0, 4), rng.uniform(4, 24), rng.uniform(24, 72)],
            weights=[20, 50, 30]
        )[0],
        "amount_scaled": rng.choices(
            [rng.uniform(0.2, 1.5), rng.uniform(1.5, 10), rng.uniform(10, 100)],
            weights=[60, 30, 10]
        )[0],
        "has_alt_upi": 1.0 if rng.random() < 0.32 else 0.0,
        "has_alt_card": 1.0 if rng.random() < 0.22 else 0.0,
        "consecutive_failures": float(rng.choices([0, 1, 2, 3, 4], weights=[50, 25, 14, 7, 4])[0]),
        "issuer_is_sbi": issuer_is_sbi,
        "issuer_is_bandhan": issuer_is_bandhan,
        "issuer_is_jio": issuer_is_jio,
        "issuer_is_hdfc": issuer_is_hdfc,
        "bias": 1.0,
    }

    # Label derivation (domain-calibrated rules → labels for logistic regression)
    soft = 1

    # Hard signals
    if feats["prior_hard_declines"] >= 2:
        soft = 0
    elif feats["consecutive_failures"] >= 3 and feats["prior_soft_recoveries"] == 0:
        soft = 0
    # HDFC "do_not_honor" is more likely fraud (low TD bank → genuine fraud signal)
    elif issuer_is_hdfc and feats["prior_hard_declines"] >= 1 and feats["prior_soft_recoveries"] == 0:
        soft = 0
    # High-TD issuers: ambiguous codes lean soft (likely technical, not fraud)
    elif (issuer_is_sbi or issuer_is_bandhan or issuer_is_jio) and feats["prior_hard_declines"] == 0:
        soft = 1
    # Recovery history is a strong positive signal
    elif feats["prior_soft_recoveries"] >= 2 and feats["prior_hard_declines"] == 0:
        soft = 1
    elif (
        feats["prior_soft_recoveries"] + (1 if feats["hours_since_last_attempt"] > 12 else 0)
    ) <= (feats["prior_hard_declines"] + 0.5 * (feats["attempt_number"] - 1)):
        soft = 0

    # Recoverability: real calibration
    if soft == 0:
        # Hard-classified: low base, slightly higher if recovery history
        recov = rng.uniform(0.03, 0.18) + 0.05 * min(feats["prior_soft_recoveries"], 2)
    else:
        # Soft-classified: recovery depends on history and attempt count
        base = 0.55
        base += 0.10 * min(feats["prior_soft_recoveries"], 3)
        base -= 0.10 * (feats["attempt_number"] - 1)
        base -= 0.05 * feats["consecutive_failures"]
        # High-TD issuers → slightly higher base recoverability (technical, not fraud)
        if issuer_is_sbi or issuer_is_bandhan or issuer_is_jio:
            base += 0.08
        recov = rng.uniform(base - 0.10, min(0.92, base + 0.10))

    recov = max(0.02, min(0.95, recov))
    return {"features": feats, "soft": soft, "recoverability": recov}


def evaluate_model(bundle: dict, samples: list[dict]) -> dict[str, float]:
    """
    Simple evaluation: accuracy, soft recall, hard recall, recoverability calibration.
    Recoverability is computed using the same proba_soft-based approach as _model_ambiguous.
    """
    clf_w = bundle["clf_weights"]
    fn = FEATURE_NAMES

    correct = 0
    true_soft = soft_correct = 0
    true_hard = hard_correct = 0
    soft_recov_sum = hard_recov_sum = 0.0
    soft_recov_n = hard_recov_n = 0

    for s in samples:
        feats = s["features"]
        y = s["soft"]
        z = sum(clf_w.get(k, 0.0) * feats.get(k, 0.0) for k in fn)
        proba_soft = _sigmoid(z)
        pred_soft = 1 if proba_soft >= 0.58 else 0  # match classify.py threshold

        # Match the inference code in classify._model_ambiguous
        base_recov = proba_soft * 0.82
        cf_penalty = feats.get("consecutive_failures", 0.0) * 0.07
        attempt_penalty = max(0.0, feats.get("attempt_number", 1.0) - 1) * 0.08
        pred_recov = max(0.04, min(0.90, base_recov - cf_penalty - attempt_penalty))

        if pred_soft == y:
            correct += 1
        if y == 1:
            true_soft += 1
            if pred_soft == 1:
                soft_correct += 1
            soft_recov_sum += pred_recov
            soft_recov_n += 1
        else:
            true_hard += 1
            if pred_soft == 0:
                hard_correct += 1
            hard_recov_sum += pred_recov
            hard_recov_n += 1

    n = len(samples)
    return {
        "accuracy": round(correct / n, 4),
        "soft_recall": round(soft_correct / true_soft, 4) if true_soft else 0,
        "hard_recall": round(hard_correct / true_hard, 4) if true_hard else 0,
        "avg_recov_soft": round(soft_recov_sum / soft_recov_n, 4) if soft_recov_n else 0,
        "avg_recov_hard": round(hard_recov_sum / hard_recov_n, 4) if hard_recov_n else 0,
        "n_samples": n,
        "n_soft": true_soft,
        "n_hard": true_hard,
    }


def run_training(*, train_seed: int = 7, n_train: int = 3000, n_test: int = 600) -> dict:
    """Train model and return metrics + top weights (for API / live UI)."""
    rng = random.Random(train_seed)
    all_samples = [_make_sample(rng) for _ in range(n_train + n_test)]
    train_samples = all_samples[:n_train]
    test_samples = all_samples[n_train:]

    bundle = train_ambiguous_model(train_samples)
    metrics = evaluate_model(bundle, test_samples)

    clf_w = bundle["clf_weights"]
    top_weights = sorted(clf_w.items(), key=lambda x: abs(x[1]), reverse=True)[:8]
    feature_weights = [
        {"feature": feat, "weight": round(w, 4), "direction": "soft" if w > 0 else "hard"}
        for feat, w in top_weights if feat != "bias"
    ]

    return {
        "train_seed": train_seed,
        "n_train": n_train,
        "n_test": n_test,
        "metrics": metrics,
        "feature_weights": feature_weights,
        "model_path": str(ROOT / "data" / "models" / "ambiguous_clf.json"),
        "quality_passed": metrics["accuracy"] >= 0.72 and metrics["hard_recall"] >= 0.60,
    }


def main() -> None:
    result = run_training()
    metrics = result["metrics"]
    print(f"Generating {result['n_train']} training + {result['n_test']} test samples...")
    print(f"\n── Evaluation on held-out test set ──")
    print(f"  Accuracy:          {metrics['accuracy']:.1%}")
    print(f"  Soft recall:       {metrics['soft_recall']:.1%}")
    print(f"  Hard recall:       {metrics['hard_recall']:.1%}")
    print(f"  Avg recov (soft):  {metrics['avg_recov_soft']:.3f}")
    print(f"  Avg recov (hard):  {metrics['avg_recov_hard']:.3f}")
    print(f"\n  Samples: {metrics['n_samples']} ({metrics['n_soft']} soft / {metrics['n_hard']} hard)")
    print("\n── Top feature weights ──")
    for fw in result["feature_weights"][:5]:
        print(f"  {fw['feature']:<35} {fw['weight']:+.4f}  → {fw['direction']}")
    print(f"\nModel saved to {result['model_path']}")
    assert result["quality_passed"]
    print("\n✓ Quality thresholds passed (accuracy ≥ 72%, hard recall ≥ 60%)")


if __name__ == "__main__":
    main()
