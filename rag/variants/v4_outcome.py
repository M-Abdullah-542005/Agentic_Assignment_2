"""V4 (ranking group): outcome-aware. Hybrid (optionally cross-encoder-reranked) relevance, then a boost for
evidence from past proposals that WON or got a REPLY (and an optional penalty for ignored ones).

final = relevance / max(relevance) + boost[status]   (only for chunks with type == 'proposal')"""
from __future__ import annotations

from rag.variants.base import Scored
from rag.variants.v2_hybrid import V2Hybrid
from rag.variants.v3_rerank import V3Rerank

DEFAULT_BOOST = {"won": 0.35, "replied": 0.15, "ignored": 0.0}


class V4Outcome(V2Hybrid):
    name = "v4_outcome"

    def __init__(self, *a, boosts: dict[str, float] | None = None, rerank: bool = False, **kw):
        super().__init__(*a, **kw)
        self.boosts = {**DEFAULT_BOOST, **(boosts or {})}
        self._rerank = V3Rerank(*a, **kw) if rerank else None

    def scored(self, job, n) -> Scored:
        base = self._rerank.scored(job, self.pool) if self._rerank else super().scored(job, self.pool)
        if not base:
            return []
        top = max(abs(s) for _, s, _ in base) or 1.0
        out = []
        for i, s, info in base:
            c = self.index.chunks[i]
            boost = self.boosts.get(c.outcome, 0.0) if c.type == "proposal" else 0.0
            out.append((i, s / top + boost, {**info, "relevance": s / top, "outcome_boost": boost}))
        return sorted(out, key=lambda x: -x[1])[:n]
