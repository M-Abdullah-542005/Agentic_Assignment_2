"""Parse a persona directory into SourceDocs (one per case study / CV section / proposal).

data/personas/{persona_id}/
    profile.json            {"skills":[{"name","verified"}...]}  (skill vocabulary)
    cv.md                   markdown, one document per heading
    case_studies/{id}.md    "# Case Study ID: Title", "**Key**: value" lines, "### Section" blocks
    proposals/history.json  list (or {"proposals":[...]}) of {id, job_title, job_description, text, status, date, skills, client_industry}
    portfolio_evidence.json list of {evidence_id, title, skills, outcome, ...} (joined to case studies by id)
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag.text import detect_skills, infer_industry

CLIENT_SECTIONS = {"client requirement", "job"}  # the CLIENT's words: good for matching, not proof of capability
_STATUS = {"won": "won", "hired": "won", "accepted": "won", "replied": "replied", "reply": "replied", "interview": "replied",
           "ignored": "ignored", "no_reply": "ignored", "viewed": "ignored", "lost": "ignored", "declined": "ignored"}


@dataclass
class SourceDoc:
    source_id: str
    evidence_id: str
    persona: str
    type: str
    project: str
    skills: list[str]
    date: str | None
    client_industry: str
    outcome: str
    sections: list[tuple[str, str]]
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def signature(self) -> str:
        blob = json.dumps([self.evidence_id, self.type, self.project, self.skills, self.date, self.client_industry,
                           self.outcome, self.sections], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()


_HEADER = re.compile(r"^#\s*Case Study\s+([^:]+):\s*(.+?)\s*$", re.M)
_META = re.compile(r"^\*\*(.+?)\*\*:\s*(.*?)\s*$", re.M)
_SECTION = re.compile(r"^#{2,4}\s+(.+?)\s*$", re.M)


def parse_case_study(text: str, fallback_id: str) -> dict[str, Any]:
    m = _HEADER.search(text)
    cs_id, title = (m.group(1).strip(), m.group(2).strip()) if m else (fallback_id, fallback_id)
    meta = {k.strip().lower(): v.strip() for k, v in _META.findall(text)}
    sections: list[tuple[str, str]] = []
    marks = list(_SECTION.finditer(text))
    for i, sm in enumerate(marks):
        body = text[sm.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip()
        if body:
            sections.append((sm.group(1).strip().lower(), body))
    if not sections:  # no headings: whole body is one section
        body = _META.sub("", _HEADER.sub("", text)).strip()
        if body:
            sections.append(("body", body))
    return {"id": cs_id, "title": html.unescape(title), "meta": meta, "sections": sections}


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _skill_vocab(root: Path, evidence: list[dict[str, Any]]) -> list[str]:
    prof = _read_json(root / "profile.json", {})
    vocab = {s["name"] for s in prof.get("skills", []) if isinstance(s, dict) and "name" in s}
    for e in evidence:
        vocab.update(e.get("skills", []))
    return sorted(vocab, key=str.lower)


def load_cv(root: Path, persona: str, vocab: list[str]) -> list[SourceDoc]:
    path = root / "cv.md"
    if not path.exists():
        return []
    text, docs = path.read_text(encoding="utf-8"), []
    marks = list(re.finditer(r"^#{1,3}\s+(.+?)\s*$", text, re.M))
    for i, hm in enumerate(marks):
        body = text[hm.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip()
        if len(body) < 20:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", hm.group(1).lower()).strip("-")
        docs.append(SourceDoc(f"cv:{slug}", f"cv:{slug}", persona, "cv", hm.group(1), detect_skills(body, vocab), None,
                              "general", "", [("cv", body)], "cv.md"))
    return docs


def load_case_studies(root: Path, persona: str, evidence: list[dict[str, Any]], vocab: list[str]) -> list[SourceDoc]:
    by_id = {e["evidence_id"]: e for e in evidence if "evidence_id" in e}
    docs = []
    for p in sorted((root / "case_studies").glob("*.md")) if (root / "case_studies").exists() else []:
        cs = parse_case_study(p.read_text(encoding="utf-8"), p.stem)
        ev, meta = by_id.get(cs["id"]) or by_id.get(p.stem) or {}, cs["meta"]
        skills = ev.get("skills") or [s.strip() for s in meta.get("skills used", "").split(",") if s.strip()]
        outcome = ev.get("outcome") or next((t for n, t in cs["sections"] if "outcome" in n), "")
        req = next((t for n, t in cs["sections"] if n in CLIENT_SECTIONS), "")
        industry = meta.get("client industry") or meta.get("industry") or infer_industry(cs["title"], req)
        eid = ev.get("evidence_id") or cs["id"]
        docs.append(SourceDoc(f"case:{eid}", eid, persona, "case_study", html.unescape(ev.get("title") or cs["title"]),
                              skills or detect_skills(" ".join(t for _, t in cs["sections"]), vocab),
                              meta.get("date") or ev.get("date"), industry, outcome, cs["sections"], f"case_studies/{p.name}"))
    return docs


def normalise_status(raw: Any) -> str:
    return _STATUS.get(str(raw or "").strip().lower().replace(" ", "_"), "ignored" if raw else "unknown")


def load_proposals(root: Path, persona: str, vocab: list[str]) -> list[SourceDoc]:
    raw = _read_json(root / "proposals" / "history.json", [])
    items = raw.get("proposals", []) if isinstance(raw, dict) else raw
    docs = []
    for i, it in enumerate(items):
        pid = str(it.get("proposal_id") or it.get("id") or f"p{i:04d}")
        text = it.get("text") or it.get("cover_letter") or it.get("proposal") or it.get("body") or ""
        if not text.strip():
            continue
        status = normalise_status(it.get("status") or it.get("label") or it.get("outcome"))
        title = it.get("job_title") or it.get("title") or pid
        jd = it.get("job_description") or it.get("job_text") or ""
        sections = [("proposal", text)] + ([("job", jd)] if jd else [])
        docs.append(SourceDoc(f"proposal:{pid}", f"proposal:{pid}", persona, "proposal", title,
                              it.get("skills") or detect_skills(text + " " + jd, vocab),
                              it.get("date") or it.get("submitted_at"), it.get("client_industry") or infer_industry(title, jd),
                              status, sections, "proposals/history.json"))
    return docs


def load_persona_docs(persona_dir: str | Path, persona: str | None = None) -> list[SourceDoc]:
    root = Path(persona_dir)
    persona = persona or root.name
    evidence = _read_json(root / "portfolio_evidence.json", [])
    vocab = _skill_vocab(root, evidence)
    return load_case_studies(root, persona, evidence, vocab) + load_cv(root, persona, vocab) + load_proposals(root, persona, vocab)
