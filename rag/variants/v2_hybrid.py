"""V2 (required): BM25 + dense similarity fused with Reciprocal Rank Fusion."""
from __future__ import annotations

from rag.variants.base import BaseRetriever, Scored, job_text, rrf


class V2Hybrid(BaseRetriever):
    name = "v2_hybrid"

    def scored(self, job, n) -> Scored:
        q = job_text(job)
        dense = [i for i, _ in self.index.dense(self.embed_query(q), max(n, 50))]
        lex = [i for i, _ in self.index.lexical(q, max(n, 50))]
        fused = rrf([dense, lex], self.rrf_k)
        d_rank, l_rank = {i: r for r, i in enumerate(dense, 1)}, {i: r for r, i in enumerate(lex, 1)}
        return [(i, s, {"dense_rank": d_rank.get(i), "bm25_rank": l_rank.get(i)})
                for i, s in sorted(fused.items(), key=lambda x: -x[1])[:n]]
