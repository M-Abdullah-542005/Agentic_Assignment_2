"""
judge_calibration.py – LLM-as-Judge Calibration Runner
Measures alignment between the LLM judge and human annotations on the dev set.
Outputs calibration metrics: accuracy, Cohen's κ, over/under-bid rates.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parents[2] / "evals" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Calibration helpers
# ---------------------------------------------------------------------------

def cohens_kappa(y_true: list[str], y_pred: list[str], labels: list[str]) -> float:
    """Compute Cohen's κ for categorical agreement."""
    n = len(y_true)
    if n == 0:
        return 0.0
    label_idx = {l: i for i, l in enumerate(labels)}
    k = len(labels)
    conf = [[0] * k for _ in range(k)]
    for yt, yp in zip(y_true, y_pred):
        if yt in label_idx and yp in label_idx:
            conf[label_idx[yt]][label_idx[yp]] += 1
    p_o = sum(conf[i][i] for i in range(k)) / n
    p_e = sum(
        (sum(conf[i][j] for j in range(k)) / n) * (sum(conf[j][i] for j in range(k)) / n)
        for i in range(k)
    )
    return (p_o - p_e) / (1 - p_e + 1e-9)


def accuracy(y_true: list[str], y_pred: list[str]) -> float:
    if not y_true:
        return 0.0
    return sum(a == b for a, b in zip(y_true, y_pred)) / len(y_true)


def precision_recall_f1(y_true: list[str], y_pred: list[str], pos: str = "bid") -> dict:
    tp = sum(a == pos and b == pos for a, b in zip(y_true, y_pred))
    fp = sum(a != pos and b == pos for a, b in zip(y_true, y_pred))
    fn = sum(a == pos and b != pos for a, b in zip(y_true, y_pred))
    p = tp / (tp + fp + 1e-9)
    r = tp / (tp + fn + 1e-9)
    f1 = 2 * p * r / (p + r + 1e-9)
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


# ---------------------------------------------------------------------------
# Judge runner
# ---------------------------------------------------------------------------

def run_judge_on_sample(sample: dict[str, Any]) -> str:
    """
    Call the LLM judge on a single dev-set item.
    Returns one of: "bid" | "skip" | "watch"
    
    In production this calls the LLM judge node from the bid graph.
    Here we use the heuristic from decision_eval.py as a stand-in.
    """
    fit = sample.get("fit_score", 0.5)
    risk = sample.get("risk_score", 0.5)
    if fit >= 0.7 and risk <= 0.4:
        return "bid"
    elif risk >= 0.7:
        return "skip"
    else:
        return "watch"


def calibrate(dev_set: list[dict[str, Any]]) -> dict:
    """Run judge over dev set and compute calibration metrics."""
    y_true = [s["gold_decision"] for s in dev_set]
    y_pred = [run_judge_on_sample(s) for s in dev_set]

    labels = ["bid", "skip", "watch"]
    kappa = cohens_kappa(y_true, y_pred, labels)
    acc = accuracy(y_true, y_pred)
    prf = precision_recall_f1(y_true, y_pred, pos="bid")

    # Bias analysis
    over_bid = sum(a == "skip" and b == "bid" for a, b in zip(y_true, y_pred))
    under_bid = sum(a == "bid" and b == "skip" for a, b in zip(y_true, y_pred))

    # Per-label breakdown
    per_label = {}
    for lbl in labels:
        per_label[lbl] = precision_recall_f1(
            [y for y in y_true if y == lbl or True],  # all items
            y_pred,
            pos=lbl,
        )

    return {
        "n": len(dev_set),
        "accuracy": round(acc, 4),
        "cohens_kappa": round(kappa, 4),
        "bid_metrics": prf,
        "over_bid_count": over_bid,
        "under_bid_count": under_bid,
        "per_label": per_label,
        "y_true": y_true,
        "y_pred": y_pred,
    }


def load_dev_set(path: str | None = None) -> list[dict]:
    if path is None:
        path = str(Path(__file__).parents[1] / "datasets" / "dev_set.json")
    with open(path) as fh:
        return json.load(fh)


def main():
    import argparse, time

    parser = argparse.ArgumentParser(description="LLM-as-judge calibration")
    parser.add_argument("--dev-set", default=None, help="Path to dev_set.json")
    parser.add_argument("--out", default=str(RESULTS_DIR / "judge_calibration.json"))
    args = parser.parse_args()

    dev_set = load_dev_set(args.dev_set)
    print(f"Calibrating on {len(dev_set)} items…")

    metrics = calibrate(dev_set)
    result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "metrics": metrics,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    print(f"Results written to {out_path}")
    print(f"  Accuracy:      {metrics['accuracy']:.4f}")
    print(f"  Cohen's κ:     {metrics['cohens_kappa']:.4f}")
    print(f"  Bid F1:        {metrics['bid_metrics']['f1']:.4f}")
    print(f"  Over-bid:      {metrics['over_bid_count']}")
    print(f"  Under-bid:     {metrics['under_bid_count']}")


if __name__ == "__main__":
    main()
