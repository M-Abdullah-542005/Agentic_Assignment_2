"""Persistent chunk index with incremental re-indexing.

``sync`` compares each source document's signature with the stored manifest and only re-chunks and
re-embeds documents that are new or changed; removed documents are dropped. A change of embedder
(name/dim) triggers a full rebuild."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from rag.bm25 import BM25
from rag.embeddings import Embedder
from rag.ingest.chunking import CHUNKERS
from rag.ingest.loaders import SourceDoc, load_persona_docs
from rag.text import tokenize
from rag.types import EvidenceChunk


@dataclass
class SyncStats:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    chunks_embedded: int = 0
    full_rebuild: bool = False


class ChunkIndex:
    def __init__(self, embedder: Embedder, chunking: str = "structured"):
        if chunking not in CHUNKERS:
            raise ValueError(f"chunking must be one of {sorted(CHUNKERS)}")
        self.embedder, self.chunking = embedder, chunking
        self.chunks: list[EvidenceChunk] = []
        self.emb = np.zeros((0, embedder.dim), dtype=np.float32)
        self.manifest: dict[str, dict] = {}
        self._bm25: BM25 | None = None

    # ---- search primitives
    def dense(self, query_vec: np.ndarray, n: int) -> list[tuple[int, float]]:
        if not len(self.chunks):
            return []
        sims = self.emb @ query_vec
        idx = np.argsort(-sims)[:n]
        return [(int(i), float(sims[i])) for i in idx]

    def bm25(self) -> BM25:
        if self._bm25 is None:
            self._bm25 = BM25([tokenize(c.embed_text()) for c in self.chunks])
        return self._bm25

    def lexical(self, query: str, n: int) -> list[tuple[int, float]]:
        s = self.bm25().scores(tokenize(query))
        idx = [int(i) for i in np.argsort(-s)[:n] if s[i] > 0]
        return [(i, float(s[i])) for i in idx]

    # ---- incremental sync
    def sync(self, docs: list[SourceDoc]) -> SyncStats:
        st, chunker = SyncStats(), CHUNKERS[self.chunking]
        if self.chunks and self.emb.shape[1] != self.embedder.dim:
            self.chunks, self.emb, self.manifest, st.full_rebuild = [], np.zeros((0, self.embedder.dim), np.float32), {}, True
        current = {d.source_id: d for d in docs}
        st.removed = sorted(set(self.manifest) - set(current))
        keep_src, redo = set(), []
        for sid, d in current.items():
            old = self.manifest.get(sid)
            if old is None:
                st.added.append(sid); redo.append(d)
            elif old["sig"] != d.signature:
                st.updated.append(sid); redo.append(d)
            else:
                keep_src.add(sid); st.unchanged += 1
        keep_idx = [i for i, c in enumerate(self.chunks) if c.chunk_id.rsplit("::", 1)[0] in keep_src]
        chunks = [self.chunks[i] for i in keep_idx]
        emb = self.emb[keep_idx] if keep_idx else np.zeros((0, self.embedder.dim), np.float32)
        manifest = {s: self.manifest[s] for s in keep_src}
        new_chunks: list[EvidenceChunk] = []
        for d in redo:
            cs = chunker(d)
            new_chunks += cs
            manifest[d.source_id] = {"sig": d.signature, "n": len(cs)}
        if new_chunks:
            vecs = self.embedder.embed([c.embed_text() for c in new_chunks])
            emb, st.chunks_embedded = np.vstack([emb, vecs]), len(new_chunks)
        self.chunks, self.emb, self.manifest, self._bm25 = chunks + new_chunks, emb, manifest, None
        return st

    # ---- persistence
    def save(self, directory: str | Path) -> None:
        d = Path(directory); d.mkdir(parents=True, exist_ok=True)
        (d / "chunks.json").write_text(json.dumps([c.to_dict() for c in self.chunks]))
        (d / "manifest.json").write_text(json.dumps({"embedder": self.embedder.name, "dim": self.embedder.dim,
                                                     "chunking": self.chunking, "sources": self.manifest}))
        np.save(d / "emb.npy", self.emb)

    def load(self, directory: str | Path) -> bool:
        d = Path(directory)
        if not (d / "manifest.json").exists():
            return False
        man = json.loads((d / "manifest.json").read_text())
        if man["embedder"] != self.embedder.name or man["chunking"] != self.chunking:
            return False  # different embedder/chunking -> start fresh
        self.chunks = [EvidenceChunk(**c) for c in json.loads((d / "chunks.json").read_text())]
        self.emb, self.manifest, self._bm25 = np.load(d / "emb.npy"), man["sources"], None
        return True


def build_index(persona_dir: str | Path, embedder: Embedder, chunking: str = "structured",
                index_dir: str | Path | None = None, persona: str | None = None) -> tuple[ChunkIndex, SyncStats]:
    """Load (if persisted), incrementally sync with the files on disk, persist, return index + stats."""
    idx = ChunkIndex(embedder, chunking)
    store = Path(index_dir) / (persona or Path(persona_dir).name) / chunking if index_dir else None
    if store:
        idx.load(store)
    stats = idx.sync(load_persona_docs(persona_dir, persona))
    if store:
        idx.save(store)
    return idx, stats
