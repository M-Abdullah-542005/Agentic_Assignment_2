"""V3 (required): hybrid candidate pool re-scored by a cross-encoder (query and chunk read jointly).

``reranker: "lexical"`` is a deterministic offline stand-in. For the real thing set
``rag.reranker: st:cross-encoder/ms-marco-MiniLM-L-6-v2`` (needs sentence-transformers)."""
from __future__ import annotations

import logging
from typing import Protocol

from rag.text import tokenize
from rag.variants.base import BaseRetriever, Scored, job_text
from rag.variants.v2_hybrid import V2Hybrid

log = logging.getLogger(__name__)


class CrossScorer(Protocol):
    name: str
    def score(self, query: str, docs: list[str]) -> list[float]: ...


class LexicalCrossScorer:
    """Joint query/document scorer: length-normalised unigram + bigram overlap. Not neural."""
    name = "lexical"

    def score(self, query: str, docs: list[str]) -> list[float]:
        q = tokenize(query); qs, qb = set(q), set(zip(q, q[1:]))
        out = []
        for d in docs:
            t = tokenize(d); ts, tb = set(t), set(zip(t, t[1:]))
            uni = len(qs & ts) / ((len(qs) * len(ts)) ** 0.5 or 1)
            bi = len(qb & tb) / ((len(qb) * len(tb)) ** 0.5 or 1)
            out.append(uni + 0.5 * bi)
        return out


class STCrossScorer:
    def __init__(self, model: str):
        from sentence_transformers import CrossEncoder
        self.model, self.name = CrossEncoder(model), f"st:{model}"

    def score(self, query: str, docs: list[str]) -> list[float]:
        return [float(s) for s in self.model.predict([(query, d) for d in docs], show_progress_bar=False)]


def get_cross_scorer(spec: str = "lexical") -> CrossScorer:
    if spec == "lexical":
        return LexicalCrossScorer()
    if spec.startswith("st:"):
        try:
            return STCrossScorer(spec[3:])
        except Exception as exc:  # missing package / no network: do NOT silently change the benchmark
            raise RuntimeError(f"could not load cross-encoder '{spec}': {exc}. Install sentence-transformers or use 'lexical'.") from exc
    raise ValueError(f"unknown reranker '{spec}'")


class V3Rerank(V2Hybrid):
    name = "v3_rerank"

    def __init__(self, *a, scorer: CrossScorer | None = None, **kw):
        super().__init__(*a, **kw)
        self.scorer = scorer or get_cross_scorer(self.opts.get("reranker", "lexical"))

    def scored(self, job, n) -> Scored:
        cands = super().scored(job, self.pool)
        if not cands:
            return []
        scores = self.scorer.score(job_text(job), [self.index.chunks[i].embed_text() for i, _, _ in cands])
        ranked = sorted(((i, sc, {**info, "cross_scorer": self.scorer.name}) for (i, _, info), sc in zip(cands, scores)),
                        key=lambda x: -x[1])
        return ranked[:n]
