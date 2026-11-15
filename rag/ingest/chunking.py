from __future__ import annotations

import re

from rag.ingest.loaders import SourceDoc
from rag.types import EvidenceChunk


def _mk(doc: SourceDoc, i: int, text: str, section: str) -> EvidenceChunk:
    return EvidenceChunk(chunk_id=f"{doc.source_id}::{i}", evidence_id=doc.evidence_id, persona=doc.persona, type=doc.type,
                         project=doc.project, skills=list(doc.skills), date=doc.date, client_industry=doc.client_industry,
                         outcome=doc.outcome, text=text.strip(), section=section, source=doc.source)


def _pack(paragraphs: list[str], limit: int) -> list[str]:
    out, cur = [], ""
    for p in paragraphs:
        while len(p) > limit:  # hard-split very long paragraphs on whitespace
            cut = p.rfind(" ", 0, limit) or limit
            out.append(p[:cut]); p = p[cut:].strip()
        if cur and len(cur) + len(p) + 1 > limit:
            out.append(cur); cur = ""
        cur = f"{cur}\n{p}".strip()
    return out + ([cur] if cur else [])


def chunk_structured(doc: SourceDoc, max_chars: int = 800) -> list[EvidenceChunk]:
    """One chunk per section (split on paragraphs when long): keeps outcome/architecture/requirement separable."""
    chunks: list[EvidenceChunk] = []
    for name, body in doc.sections:
        for piece in _pack([p for p in re.split(r"\n\s*\n", body) if p.strip()], max_chars):
            chunks.append(_mk(doc, len(chunks), piece, name))
    return chunks


def chunk_fixed(doc: SourceDoc, size: int = 500, overlap: int = 100) -> list[EvidenceChunk]:
    """Baseline chunking: sections concatenated, then fixed character windows with overlap."""
    text = "\n".join(f"{n}: {b}" for n, b in doc.sections)
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            end = text.rfind(" ", start, end) if text.rfind(" ", start, end) > start else end
        chunks.append(_mk(doc, len(chunks), text[start:end], "fixed"))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


CHUNKERS = {"structured": chunk_structured, "fixed": chunk_fixed}
