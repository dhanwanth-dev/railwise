"""Train ambiguous-code logistic model on synthetic labeled features."""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.classify import FEATURE_NAMES, train_ambiguous_model  # noqa: E402


def main() -> None:
    rng = random.Random(7)
    samples = []
    for _ in range(2000):
        is_upi = 1.0 if rng.random() < 0.5 else 0.0
        feats = {
            "is_upi": is_upi,
            "is_card": 1.0 - is_upi,
            "attempt_number": float(rng.randint(1, 4)),
            "prior_soft_recoveries": float(rng.randint(0, 4)),
            "prior_hard_declines": float(rng.randint(0, 3)),
            "hours_since_last_attempt": rng.uniform(0, 72),
            "amount_scaled": rng.uniform(0.1, 20),
            "has_alt_upi": 1.0 if rng.random() < 0.3 else 0.0,
            "has_alt_card": 1.0 if rng.random() < 0.3 else 0.0,
            "bias": 1.0,
        }
        soft = 1
        if feats["prior_hard_declines"] >= 2:
            soft = 0
        elif feats["prior_soft_recoveries"] >= 2 and feats["prior_hard_declines"] == 0:
            soft = 1
        elif (feats["prior_soft_recoveries"] + (1 if feats["hours_since_last_attempt"] > 12 else 0)) <= (
            feats["prior_hard_declines"] + 0.5 * (feats["attempt_number"] - 1)
        ):
            soft = 0
        recov = 0.1 if soft == 0 else min(0.9, 0.4 + 0.12 * feats["prior_soft_recoveries"] - 0.1 * (feats["attempt_number"] - 1))
        samples.append({"features": feats, "soft": soft, "recoverability": recov})

    train_ambiguous_model(samples)
    print(f"Trained logistic model with {len(FEATURE_NAMES)} features on {len(samples)} rows → data/models/ambiguous_clf.json")


if __name__ == "__main__":
    main()
