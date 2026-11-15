"""V1 (required): decompose the job post into sub-requirements, query each against the evidence, merge.

Decomposition is rule-based by default (sentence/bullet cues + the job's skill tags). Pass
``decomposer=callable(job)->list[str]`` to use an LLM instead. Merge = RRF across per-requirement rankings,
so evidence that answers several requirements outranks evidence matching only one."""
from __future__ import annotations

import re
from typing import Any, Callable

from rag.text import split_sentences, tokenize
from rag.variants.base import BaseRetriever, Scored, job_text, rrf

_CUE = re.compile(r"\b(experience|proficien|must|required?|requirements?|need|looking for|seeking|build|develop|deploy|integrat|"
                  r"implement|design|knowledge|familiar|skills?|expertise|ability|responsible|maintain|optimi[sz]e|migrate)\w*", re.I)


def extract_requirements(job: dict[str, Any] | str, max_n: int = 8) -> list[str]:
    desc = job if isinstance(job, str) else (job.get("description") or job.get("job_text") or job.get("text") or "")
    reqs: list[str] = []
    for line in re.split(r"\n+|(?<=\.)\s+(?=[A-Z])", desc):
        line = re.sub(r"^\s*[-*•\d.)]+\s*", "", line).strip()
        for s in split_sentences(line) or [line]:
            if 12 <= len(s) <= 300 and (_CUE.search(s) or line != s):
                reqs.append(s)
    covered = " ".join(reqs).lower()
    skills = [] if isinstance(job, str) else (job.get("skills") or [])
    reqs += [f"Experience with {s}" for s in skills if s.lower() not in covered]
    title = "" if isinstance(job, str) else job.get("title", "")
    seen, out = set(), []
    for r in ([title] if title else []) + reqs:
        key = tuple(tokenize(r))
        if key and key not in seen:
            seen.add(key); out.append(r)
    return out[:max_n] or [job_text(job)]


class V1Decomp(BaseRetriever):
    name = "v1_decomp"

    def __init__(self, *a, decomposer: Callable[[Any], list[str]] | None = None, per_req: int = 10, **kw):
        super().__init__(*a, **kw)
        self.decomposer, self.per_req = decomposer or extract_requirements, per_req

    def scored(self, job, n) -> Scored:
        reqs = self.decomposer(job)
        lists, hits = [], {}
        for r_i, r in enumerate(reqs):
            ranked = [i for i, _ in self.index.dense(self.embed_query(r), self.per_req)]
            lists.append(ranked)
            for i in ranked:
                hits.setdefault(i, []).append(reqs[r_i])
        fused = rrf(lists, self.rrf_k)
        return [(i, s, {"matched_requirements": hits[i]}) for i, s in sorted(fused.items(), key=lambda x: -x[1])[:n]]
