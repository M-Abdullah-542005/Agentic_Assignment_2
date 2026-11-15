"""Claim verification for drafted proposals.

Pipeline: split draft -> keep sentences that make verifiable claims about the freelancer -> check each against
retrieved portfolio evidence -> label SUPPORTED / PARTLY_SUPPORTED / UNSUPPORTED (with reasons) -> strip or
rewrite weak claims -> report the unsupported-claim rate before and after.

Checking is deterministic and conservative: numbers must appear in the evidence, named technologies/entities
must appear in the evidence, and the remaining content words must be covered. Pass ``judge=`` (a callable
``(claim, evidence_texts) -> {"label": ..., "reason": ...}``, e.g. an LLM/NLI call) to override the heuristic
for sentences that contain no numbers or named terms. Client-requirement text is never accepted as evidence."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from rag.ingest.loaders import CLIENT_SECTIONS
from rag.text import split_sentences, stem, tokenize

SUPPORTED, PARTLY, UNSUPPORTED = "SUPPORTED", "PARTLY_SUPPORTED", "UNSUPPORTED"

_VERBS = {"built", "build", "delivered", "designed", "developed", "implemented", "led", "reduced", "improved", "increased",
          "deployed", "migrated", "architected", "created", "launched", "shipped", "achieved", "optimized", "optimised",
          "scaled", "managed", "wrote", "automated", "integrated", "trained", "fine-tuned", "cut", "boosted", "saved",
          "worked", "completed", "maintained", "founded", "won", "earned", "served", "generated", "processed", "handled"}
_FIRST = {"i", "my", "we", "our", "i've", "i'm", "me", "we've"}
_SKIP_START = ("hi", "hello", "dear", "thanks", "thank you", "best", "regards", "cheers", "looking forward", "let's", "lets",
               "happy to", "i'd love", "i would love", "feel free", "please", "i am excited", "i'm excited", "i am happy",
               "i'm happy", "i am confident", "i'm confident", "i'd be happy", "i can ")
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_VERB_TOKENS = {stem(v) for v in _VERBS} | _VERBS
_GENERIC = {"experience", "year", "project", "work", "client", "team", "system", "solution", "result", "great", "strong", "new",
            "key", "several", "multiple", "various", "number", "high", "large", "real"}


@dataclass
class ClaimResult:
    sentence: str
    is_claim: bool
    label: str | None = None
    reasons: list[str] = field(default_factory=list)
    action: str = "kept"                # kept | rewritten | removed | not_a_claim
    rewritten: str | None = None
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class VerificationReport:
    verified_draft: str
    claims: list[ClaimResult]
    n_claims_before: int
    unsupported_before: int
    partly_before: int
    unsupported_rate_before: float
    n_claims_after: int
    unsupported_after: int
    unsupported_rate_after: float

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy(); d["claims"] = [c.to_dict() for c in self.claims]; return d


# ------------------------------------------------------------------ evidence handling
def _norm_evidence(evidence: Iterable[Any]) -> list[dict[str, Any]]:
    out = []
    for e in evidence:
        d = e if isinstance(e, dict) else e.to_dict()
        if str(d.get("section", "")).lower() in CLIENT_SECTIONS:
            continue                      # the client's words are not proof of what the freelancer did
        out.append(d)
    return out


class _Evidence:
    def __init__(self, evidence: Iterable[Any]):
        self.items = _norm_evidence(evidence)
        self.texts = [" ".join(str(d.get(k, "")) for k in ("project", "text", "outcome")) + " " + " ".join(d.get("skills", []))
                      for d in self.items]
        blob = " ".join(self.texts)
        self.tokens = set(tokenize(blob, keep_stop=True))
        self.numbers = {self._n(n) for n in _NUM.findall(blob)}

    @staticmethod
    def _n(s: str) -> str:
        return s.replace(",", "").rstrip(".").lstrip("0") or "0"

    def ids_for(self, tokens: set[str]) -> list[str]:
        scored = [(len(tokens & set(tokenize(t))), d.get("evidence_id", "")) for t, d in zip(self.texts, self.items)]
        return [i for n, i in sorted(scored, key=lambda x: -x[0])[:2] if n]


# ------------------------------------------------------------------ claim detection + analysis
def is_claim(sentence: str) -> bool:
    s = sentence.strip()
    low = s.lower()
    if s.endswith("?") or low.startswith(_SKIP_START):
        return False
    words = re.findall(r"[a-z']+", low)
    first_person = bool(_FIRST & set(words))
    verb = bool(_VERBS & set(words))
    verifiable = bool(_NUM.search(s)) or verb or "experience" in low or "years" in low or len(_terms(s)) > 0
    return (first_person or verb) and verifiable


def _terms(sentence: str) -> list[str]:
    """Named technologies/entities: Capitalised words not at sentence start, CamelCase/ALLCAPS, tokens with digits, +, #."""
    out = []
    for i, w in enumerate(re.findall(r"[A-Za-z][A-Za-z0-9\+\#\-\.]*", sentence)):
        w = w.strip(".-")
        if not w or w.lower() in {"i", "i'm", "i've", "i'd"} or w.lower() in STOP_CAPS:
            continue
        if (w[0].isupper() and i > 0) or any(c.isupper() for c in w[1:]) or any(c.isdigit() or c in "+#" for c in w):
            out.append(w)
    return out


STOP_CAPS = {"the", "a", "an", "and", "for", "with", "on", "in", "at", "to", "as", "by", "of", "my", "our", "we", "star", "stars"}


def analyze(claim: str, ev: _Evidence) -> tuple[str, list[str], dict[str, Any]]:
    nums = [_Evidence._n(n) for n in _NUM.findall(claim)]
    miss_nums = [n for n in nums if n not in ev.numbers]
    terms = _terms(claim)
    miss_terms = [t for t in terms if not (set(tokenize(t)) & ev.tokens) or not set(tokenize(t)) <= ev.tokens]
    content = [t for t in tokenize(claim) if t not in _VERB_TOKENS and t not in _GENERIC and not t.isdigit() and t not in _FIRST]
    covered = [t for t in content if t in ev.tokens]
    coverage = len(covered) / len(content) if content else 1.0
    reasons: list[str] = []
    if miss_nums:
        reasons.append(f"number(s) not in evidence: {', '.join(miss_nums)}")
    if miss_terms:
        reasons.append(f"term(s) not in evidence: {', '.join(miss_terms)}")
    if coverage < 0.6:
        reasons.append(f"only {coverage:.0%} of content words found in evidence")
    all_nums_missing = bool(nums) and len(miss_nums) == len(nums)
    all_terms_missing = bool(terms) and len(miss_terms) == len(terms)
    if all_nums_missing or all_terms_missing or coverage < 0.3:
        label = UNSUPPORTED
    elif miss_nums or miss_terms or coverage < 0.6:
        label = PARTLY
    else:
        label = SUPPORTED
        reasons = ["all numbers, named terms and key words found in evidence"]
    info = {"missing_numbers": miss_nums, "missing_terms": miss_terms, "coverage": round(coverage, 3),
            "evidence_ids": ev.ids_for(set(covered))}
    return label, reasons, info


_SPLIT = re.compile(r"(,\s+and\s+|,\s+|\s+and\s+|\s+with\s+|\s+that\s+|\s+which\s+|\s+while\s+)")


def rewrite_partial(sentence: str, info: dict[str, Any]) -> str | None:
    """Drop clauses that contain unsupported numbers/terms; keep the lead clause only if it is itself clean."""
    bad = [x.lower() for x in info["missing_numbers"] + info["missing_terms"]]
    if not bad:
        return None
    parts = _SPLIT.split(sentence.rstrip(" .!"))
    if any(b in parts[0].lower() for b in bad):
        return None
    kept = [parts[0]]
    for delim, clause in zip(parts[1::2], parts[2::2]):
        if not any(b in clause.lower() for b in bad):
            kept += [delim, clause]
    out = "".join(kept).strip()
    return out + "." if len(out.split()) >= 4 else None


# ------------------------------------------------------------------ public API
def _label_all(sentences: list[str], ev: _Evidence, judge: Callable | None) -> list[ClaimResult]:
    res = []
    for s in sentences:
        if not is_claim(s):
            res.append(ClaimResult(s, False, action="not_a_claim"))
            continue
        label, reasons, info = analyze(s, ev)
        if judge and not info["missing_numbers"] and not info["missing_terms"]:
            j = judge(s, ev.texts)
            label, reasons = j.get("label", label), [j.get("reason", "judge decision")]
        r = ClaimResult(s, True, label, reasons, evidence_ids=info["evidence_ids"])
        r.__dict__["_info"] = info
        res.append(r)
    return res


def verify_claims(draft: str, evidence: list[Any]) -> list[dict[str, Any]]:
    """Label every sentence of ``draft`` (drop-in for the original skeleton). Labels are upper-case."""
    return [{k: v for k, v in r.to_dict().items() if k != "_info"} for r in _label_all(split_sentences(draft), _Evidence(evidence), None)]


def verify_proposal(draft: str, evidence: list[Any], judge: Callable | None = None) -> VerificationReport:
    """Verify, then strip/rewrite. UNSUPPORTED claims are removed; PARTLY_SUPPORTED claims are rewritten to their
    supported clauses when possible (otherwise kept as-is, flagged in ``reasons``)."""
    ev = _Evidence(evidence)
    results = _label_all(split_sentences(draft), ev, judge)
    claims = [r for r in results if r.is_claim]
    before_unsup = sum(r.label == UNSUPPORTED for r in claims)
    before_part = sum(r.label == PARTLY for r in claims)
    final: list[str] = []
    for r in results:
        if not r.is_claim or r.label == SUPPORTED:
            final.append(r.sentence)
        elif r.label == UNSUPPORTED:
            r.action = "removed"
        else:
            new = rewrite_partial(r.sentence, r.__dict__["_info"])
            if new and analyze(new, ev)[0] != UNSUPPORTED:
                r.action, r.rewritten = "rewritten", new
                final.append(new)
            else:
                r.action = "removed"
    verified = " ".join(final)
    after = [analyze(s, ev)[0] for s in split_sentences(verified) if is_claim(s)]
    after_unsup = sum(a == UNSUPPORTED for a in after)
    return VerificationReport(
        verified_draft=verified, claims=results, n_claims_before=len(claims), unsupported_before=before_unsup,
        partly_before=before_part, unsupported_rate_before=before_unsup / len(claims) if claims else 0.0,
        n_claims_after=len(after), unsupported_after=after_unsup,
        unsupported_rate_after=after_unsup / len(after) if after else 0.0)
