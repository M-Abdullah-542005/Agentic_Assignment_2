"""V0 (baseline, required): fixed-size chunks, dense embeddings, top-k cosine over the FULL job post."""
from __future__ import annotations

from rag.variants.base import BaseRetriever, Scored, job_text


class V0Naive(BaseRetriever):
    name, chunking = "v0_naive", "fixed"

    def scored(self, job, n) -> Scored:
        q = self.embed_query(job_text(job))
        return [(i, s, {}) for i, s in self.index.dense(q, n)]


def retrieve(query: str, index, k: int = 5, embedder=None):
    """Functional entry point matching the original skeleton signature: ``retrieve(query, index, k)``."""
    from rag.embeddings import get_embedder
    return V0Naive(index, embedder or get_embedder("hashing"), max_per_evidence=1).retrieve(query, k)
