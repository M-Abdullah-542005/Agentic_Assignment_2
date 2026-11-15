"""
gigsim_outcomes.py – GigSim Integration Outcome Evaluator
Runs the BidGraph against GigSim marketplace and collects bid/skip/hire outcomes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parents[2] / "evals" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class JobOutcome:
    job_id: str
    title: str
    decision: str          # bid | skip | watch
    fit_score: float
    risk_score: float
    bid_amount: float | None
    connects_used: int
    hire: bool | None      # None = unknown (async)
    latency_ms: float
    error: str | None = None
    nodes_visited: list[str] = field(default_factory=list)


async def run_single_job(job: dict[str, Any], persona: str = "ai_ml_engineer") -> JobOutcome:
    """Run the bid graph against a single job spec and return the outcome."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[3] / "agent"))

    start = time.monotonic()
    try:
        from agent.graphs.bid_graph import build_graph
        from agent.state import BidState

        graph = build_graph()
        init_state = BidState(
            job_id=job["job_id"],
            job_spec=job,
            persona=persona,
        )
        result = await graph.ainvoke(init_state)
        elapsed = (time.monotonic() - start) * 1000

        return JobOutcome(
            job_id=job["job_id"],
            title=job.get("title", ""),
            decision=result.get("decision", "skip"),
            fit_score=result.get("fit_score", 0.0),
            risk_score=result.get("risk_score", 0.0),
            bid_amount=result.get("bid_amount"),
            connects_used=result.get("connects_used", 0),
            hire=None,  # asynchronous outcome
            latency_ms=elapsed,
            nodes_visited=result.get("nodes_visited", []),
        )
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        logger.exception("Job %s failed: %s", job.get("job_id"), exc)
        return JobOutcome(
            job_id=job.get("job_id", "unknown"),
            title=job.get("title", ""),
            decision="error",
            fit_score=0.0,
            risk_score=0.0,
            bid_amount=None,
            connects_used=0,
            hire=None,
            latency_ms=elapsed,
            error=str(exc),
        )


async def run_evaluation(
    jobs: list[dict],
    persona: str = "ai_ml_engineer",
    max_concurrency: int = 4,
) -> list[JobOutcome]:
    """Run evaluation across all jobs with bounded concurrency."""
    sem = asyncio.Semaphore(max_concurrency)

    async def _run(job):
        async with sem:
            return await run_single_job(job, persona)

    tasks = [_run(j) for j in jobs]
    return await asyncio.gather(*tasks)


def load_stream_jobs(n: int = 50) -> list[dict]:
    """Load jobs from GigSim stream.json."""
    stream_path = Path(__file__).parents[3] / "data" / "jobs" / "stream.json"
    if not stream_path.exists():
        raise FileNotFoundError(f"stream.json not found at {stream_path}")
    with stream_path.open() as fh:
        jobs = json.load(fh)
    return jobs[:n]


def compute_summary(outcomes: list[JobOutcome]) -> dict:
    """Compute aggregate statistics from outcomes."""
    total = len(outcomes)
    if total == 0:
        return {}
    bids = [o for o in outcomes if o.decision == "bid"]
    skips = [o for o in outcomes if o.decision == "skip"]
    errors = [o for o in outcomes if o.error is not None]
    latencies = [o.latency_ms for o in outcomes if o.error is None]

    return {
        "total": total,
        "bids": len(bids),
        "skips": len(skips),
        "errors": len(errors),
        "bid_rate": len(bids) / total,
        "avg_fit_on_bid": sum(o.fit_score for o in bids) / max(len(bids), 1),
        "avg_risk_on_bid": sum(o.risk_score for o in bids) / max(len(bids), 1),
        "avg_latency_ms": sum(latencies) / max(len(latencies), 1),
        "p95_latency_ms": sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GigSim outcome evaluator")
    parser.add_argument("--n", type=int, default=20, help="Number of jobs to evaluate")
    parser.add_argument("--persona", default="ai_ml_engineer")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", default=str(RESULTS_DIR / "gigsim_outcomes.json"))
    args = parser.parse_args()

    jobs = load_stream_jobs(args.n)
    print(f"Running evaluation on {len(jobs)} jobs (persona={args.persona})…")

    outcomes = asyncio.run(run_evaluation(jobs, args.persona, args.concurrency))
    summary = compute_summary(outcomes)

    result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "persona": args.persona,
        "summary": summary,
        "outcomes": [asdict(o) for o in outcomes],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    print(f"Results written to {out_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
