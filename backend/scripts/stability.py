#!/usr/bin/env python3
"""CLI: multi-seed stability report for Railwise vs baseline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.analytics import run_stability  # noqa: E402


def main() -> None:
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    result = run_stability(n_seeds=n_seeds, batch_size=500)
    s = result["summary"]
    print(f"=== RAILWISE STABILITY ({n_seeds} seeds × 500 events) ===")
    print(f"Wins soft rate: {s['railwise_wins_soft_rate']}/{n_seeds}")
    print(f"Avg lift:       +{s['avg_soft_delta_pp']} pp (σ={s['std_soft_delta_pp']})")
    print(f"Recovery mean:  {s['railwise_soft_recovery_mean']:.1%} (σ={s['railwise_soft_recovery_std']:.4f})")
    print(f"Zero hard waste: {s['zero_hard_wasted_all_seeds']}")
    print(f"Zero UPI viol:   {s['zero_upi_violations_all_seeds']}")
    out = ROOT / "data" / "stability_report.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\nFull report → {out}")


if __name__ == "__main__":
    main()
