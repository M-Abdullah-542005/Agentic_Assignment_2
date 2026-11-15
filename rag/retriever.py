"""Config-driven retriever factory.

agent/config.yaml:
    rag:
      variant: v2_hybrid          # v0_naive | v1_decomp | v2_hybrid | v3_rerank | v4_outcome
      data_dir: data/personas
      index_dir: var/rag_index
      default_persona: ai_ml_engineer
      embedder: hashing           # or st:BAAI/bge-small-en-v1.5
      reranker: lexical           # or st:cross-encoder/ms-marco-MiniLM-L-6-v2
      max_chunks_per_evidence: 2

Usage:  from rag.retriever import retrieve;  retrieve({"title":..., "description":..., "skills":[...]}, k=5)
A job may carry "persona_id" to pick the persona; otherwise default_persona is used."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from rag.embeddings import Embedder, get_embedder
from rag.ingest.indexer import ChunkIndex, SyncStats, build_index
from rag.types import EvidenceChunk
from rag.variants import VARIANTS


@dataclass
class RagConfig:
    variant: str = "v2_hybrid"
    data_dir: str = "data/personas"
    index_dir: str | None = "var/rag_index"
    default_persona: str = "ai_ml_engineer"
    embedder: str = "hashing"
    reranker: str = "lexical"
    max_chunks_per_evidence: int = 2
    rrf_k: int = 60
    pool: int = 30
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path = "agent/config.yaml") -> "RagConfig":
        raw = (yaml.safe_load(Path(path).read_text()) or {}).get("rag", {})
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class Retriever:
    """Holds one index per (persona, chunking) and routes ``retrieve`` to the configured variant."""

    def __init__(self, cfg: RagConfig, embedder: Embedder | None = None):
        if cfg.variant not in VARIANTS:
            raise ValueError(f"rag.variant must be one of {sorted(VARIANTS)}, got {cfg.variant!r}")
        self.cfg, self.embedder = cfg, embedder or get_embedder(cfg.embedder)
        self._impl: dict[tuple[str, str], Any] = {}
        self.last_sync: dict[str, SyncStats] = {}

    def _index(self, persona: str, chunking: str, refresh: bool = False) -> ChunkIndex:
        idx, stats = build_index(Path(self.cfg.data_dir) / persona, self.embedder, chunking, self.cfg.index_dir, persona)
        self.last_sync[f"{persona}/{chunking}"] = stats
        return idx

    def variant(self, persona: str, name: str | None = None) -> Any:
        name = name or self.cfg.variant
        cls = VARIANTS[name]
        key = (persona, name)
        if key not in self._impl:
            self._impl[key] = cls(self._index(persona, cls.chunking), self.embedder,
                                  max_per_evidence=self.cfg.max_chunks_per_evidence, pool=self.cfg.pool,
                                  rrf_k=self.cfg.rrf_k, reranker=self.cfg.reranker, **self.cfg.options.get(name, {}))
        return self._impl[key]

    def reindex(self, persona: str | None = None) -> dict[str, SyncStats]:
        """Pick up new/changed case studies and proposals (incremental) and rebuild the retrievers."""
        persona = persona or self.cfg.default_persona
        self._impl = {k: v for k, v in self._impl.items() if k[0] != persona}
        self.variant(persona)
        return {k: v for k, v in self.last_sync.items() if k.startswith(persona + "/")}

    def retrieve(self, job: dict[str, Any], k: int = 5) -> list[EvidenceChunk]:
        persona = (job.get("persona_id") if isinstance(job, dict) else None) or self.cfg.default_persona
        return self.variant(persona).retrieve(job, k)


_default: Retriever | None = None


def load_retriever(config_path: str | Path = "agent/config.yaml", **overrides: Any) -> Retriever:
    cfg = RagConfig.from_yaml(config_path)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return Retriever(cfg)


def retrieve(job: dict[str, Any], k: int = 5) -> list[EvidenceChunk]:
    """Module-level convenience: uses the variant named in agent/config.yaml."""
    global _default
    if _default is None:
        _default = load_retriever()
    return _default.retrieve(job, k)
