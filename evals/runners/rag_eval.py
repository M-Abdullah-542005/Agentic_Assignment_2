"""Retrieval benchmark: Recall@5, MRR, nDCG@10, unsupported claims per proposal (before/after verification), latency.

    python -m evals.runners.rag_eval --dataset evals/datasets/dev_eval_set.json --data-dir data/personas \
        [--variants v0_naive v1_decomp ...] [--drafter my_pkg.drafts:draft] [--out evals/results/rag_benchmark.md]

Dataset item (tolerant; see normalize_item):
    {"id": "q001", "persona_id": "ai_ml_engineer",
     "job": {"title": "...", "description": "...", "skills": ["..."]},      # or job_text / query
     "relevant_evidence_ids": ["CS-AI_-001", ...]  | {"CS-AI_-001": 3, ...} (graded),
     "draft": "optional proposal text to verify"}
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from rag.ingest.loaders import CLIENT_SECTIONS
from rag.retriever import RagConfig, Retriever
from rag.variants import VARIANTS
from rag.verify import verify_proposal

GOLD_KEYS = ("relevant_evidence_ids", "gold_evidence_ids", "relevant_ids", "relevant", "gold", "evidence_ids")


# ------------------------------------------------------------------ dataset
def normalize_item(raw: dict[str, Any], i: int = 0) -> dict[str, Any]:
    job = raw.get("job")
    if isinstance(job, str):
        job = {"description": job}
    job = dict(job or {k: raw[k] for k in ("title", "description", "skills", "job_text", "query", "text") if k in raw})
    gold_raw = next((raw[k] for k in GOLD_KEYS if k in raw), {})
    gold = {g: 1.0 for g in gold_raw} if isinstance(gold_raw, list) else {k: float(v) for k, v in gold_raw.items()}
    if "relevance" in raw and isinstance(raw["relevance"], dict):
        gold = {k: float(v) for k, v in raw["relevance"].items()}
    gold = {k: v for k, v in gold.items() if v > 0}
    return {"id": str(raw.get("id", f"q{i:03d}")), "persona_id": raw.get("persona_id") or raw.get("persona"),
            "job": job, "gold": gold, "draft": raw.get("draft")}


def load_dataset(path: str | Path) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = raw.get("items") or raw.get("examples") or raw.get("data") or [] if isinstance(raw, dict) else raw
    return [normalize_item(r, i) for i, r in enumerate(raw)]


# ------------------------------------------------------------------ metrics (evidence-id level)
def recall_at_k(ranked: list[str], gold: dict[str, float], k: int) -> float:
    return len(set(ranked[:k]) & set(gold)) / len(gold) if gold else 0.0


def mrr(ranked: list[str], gold: dict[str, float]) -> float:
    return next((1.0 / r for r, e in enumerate(ranked, 1) if e in gold), 0.0)


def ndcg_at_k(ranked: list[str], gold: dict[str, float], k: int) -> float:
    dcg = sum((2 ** gold.get(e, 0) - 1) / math.log2(r + 1) for r, e in enumerate(ranked[:k], 1))
    ideal = sorted(gold.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(r + 1) for r, g in enumerate(ideal, 1))
    return dcg / idcg if idcg else 0.0


def unique_ids(chunks) -> list[str]:
    seen, out = set(), []
    for c in chunks:
        if c.evidence_id not in seen:
            seen.add(c.evidence_id); out.append(c.evidence_id)
    return out


# ------------------------------------------------------------------ drafting
def extractive_drafter(job: dict[str, Any], chunks) -> str:
    """Offline stand-in: states only what the top evidence says. Replace with your LLM drafter (--drafter) to
    measure the real hallucination rate; datasets may also carry a ready-made ``draft`` per item."""
    parts, seen = [], set()
    for c in chunks:
        if c.evidence_id in seen or c.section in CLIENT_SECTIONS or (c.type == "cv" and not c.outcome):
            continue  # client-requirement text is not capability evidence (the verifier ignores it too)
        seen.add(c.evidence_id)
        sk = ", ".join(c.skills[:3])
        parts.append(f"I delivered {c.project} using {sk}." if sk else f"I delivered {c.project}.")
        if c.type == "case_study" and c.outcome:
            parts.append(f"The outcome was {c.outcome}.")
        if len(seen) == 3:
            break
    return " ".join(parts)


def load_callable(spec: str) -> Callable:
    mod, _, fn = spec.partition(":")
    return getattr(importlib.import_module(mod), fn)


# ------------------------------------------------------------------ benchmark
def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(math.ceil(q * len(xs)) - 1))] if xs else 0.0


def run_benchmark(dataset: list[dict[str, Any]], data_dir: str, variants: list[str] | None = None, *, index_dir: str | None = None,
                  embedder: str = "hashing", reranker: str = "lexical", default_persona: str | None = None,
                  drafter: Callable | None = None, k_draft: int = 5) -> dict[str, Any]:
    variants = variants or list(VARIANTS)
    drafter = drafter or extractive_drafter
    personas = sorted({it["persona_id"] or default_persona or "" for it in dataset})
    results: dict[str, Any] = {"variants": {}, "n_queries": len(dataset), "embedder": embedder, "reranker": reranker}
    for v in variants:
        cfg = RagConfig(variant=v, data_dir=data_dir, index_dir=index_dir, embedder=embedder, reranker=reranker,
                        default_persona=default_persona or personas[0], max_chunks_per_evidence=1)
        ret, rows = Retriever(cfg), []
        for p in personas:
            ret.variant(p)                         # build/sync index outside the timed region
        for it in dataset:
            persona = it["persona_id"] or cfg.default_persona
            job = {**it["job"], "persona_id": persona}
            t0 = time.perf_counter()
            chunks = ret.retrieve(job, k=10)
            ms = (time.perf_counter() - t0) * 1000
            ids = unique_ids(chunks)
            draft = it["draft"] or drafter(job, chunks[:k_draft])
            rep = verify_proposal(draft, chunks[:k_draft])
            rows.append({"id": it["id"], "persona": persona, "recall@5": recall_at_k(ids, it["gold"], 5), "mrr": mrr(ids, it["gold"]),
                         "ndcg@10": ndcg_at_k(ids, it["gold"], 10), "latency_ms": ms,
                         "unsupported_before": rep.unsupported_before, "unsupported_after": rep.unsupported_after,
                         "claims": rep.n_claims_before})
        results["variants"][v] = {"overall": _agg(rows), "by_persona": {p: _agg([r for r in rows if r["persona"] == p]) for p in personas},
                                  "per_query": rows}
    return results


def _agg(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {}
    m = lambda key: statistics.fmean(r[key] for r in rows)
    lat = [r["latency_ms"] for r in rows]
    return {"n": len(rows), "recall@5": m("recall@5"), "mrr": m("mrr"), "ndcg@10": m("ndcg@10"),
            "unsupported_per_proposal_before": m("unsupported_before"), "unsupported_per_proposal_after": m("unsupported_after"),
            "latency_p50_ms": _pct(lat, 0.5), "latency_p95_ms": _pct(lat, 0.95)}


def to_markdown(res: dict[str, Any]) -> str:
    L = ["# RAG benchmark", "",
         f"{res['n_queries']} queries - embedder `{res['embedder']}` - reranker `{res['reranker']}`. "
         "Relevance is judged at evidence-id level; latency excludes index build.", "",
         "| Variant | Recall@5 | MRR | nDCG@10 | Unsupported claims / proposal (before -> after verify) | Latency p50 / p95 (ms) |",
         "|---|---|---|---|---|---|"]
    for v, d in res["variants"].items():
        o = d["overall"]
        L.append(f"| {v} | {o['recall@5']:.3f} | {o['mrr']:.3f} | {o['ndcg@10']:.3f} | "
                 f"{o['unsupported_per_proposal_before']:.2f} -> {o['unsupported_per_proposal_after']:.2f} | "
                 f"{o['latency_p50_ms']:.1f} / {o['latency_p95_ms']:.1f} |")
    personas = sorted({p for d in res["variants"].values() for p in d["by_persona"]})
    if len(personas) > 1:
        L += ["", "## By persona (Recall@5 / MRR / nDCG@10)", "", "| Variant | " + " | ".join(personas) + " |", "|---|" + "---|" * len(personas)]
        for v, d in res["variants"].items():
            L.append(f"| {v} | " + " | ".join(
                f"{d['by_persona'][p]['recall@5']:.2f} / {d['by_persona'][p]['mrr']:.2f} / {d['by_persona'][p]['ndcg@10']:.2f}"
                if d["by_persona"].get(p) else "-" for p in personas) + " |")
    return "\n".join(L) + "\n"


def write_outputs(res: dict[str, Any], out_md: str | Path) -> tuple[Path, Path]:
    md = Path(out_md); md.parent.mkdir(parents=True, exist_ok=True)
    js = md.with_suffix(".json")
    md.write_text(to_markdown(res), encoding="utf-8")
    js.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return md, js


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="evals/datasets/dev_eval_set.json")
    ap.add_argument("--data-dir", default="data/personas")
    ap.add_argument("--variants", nargs="*", default=None)
    ap.add_argument("--index-dir", default="var/rag_index_eval")
    ap.add_argument("--embedder", default="hashing")
    ap.add_argument("--reranker", default="lexical")
    ap.add_argument("--drafter", default=None, help="module:function(job, chunks) -> proposal text")
    ap.add_argument("--out", default="evals/results/rag_benchmark.md")
    a = ap.parse_args(argv)
    res = run_benchmark(load_dataset(a.dataset), a.data_dir, a.variants, index_dir=a.index_dir, embedder=a.embedder,
                        reranker=a.reranker, drafter=load_callable(a.drafter) if a.drafter else None)
    md, js = write_outputs(res, a.out)
    print(to_markdown(res)); print(f"wrote {md} and {js}")


if __name__ == "__main__":
    main()
