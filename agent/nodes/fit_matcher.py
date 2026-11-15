"""Fit Matcher: uses the RAG retriever to match requirements to portfolio evidence -> FitReport."""
import re

from agent.deps import Deps, call_tool
from agent.handoffs import FitReport


def fit_node(state, deps: Deps):
    job, trace = state["job"], []
    persona = job.get("persona_id") or getattr(deps, "persona_id", None)
    query_job = {**job, "persona_id": persona} if persona else job      # retrieve from THIS user's portfolio
    evidence = call_tool(trace, "retrieve", deps.do_retrieve, job=query_job, k=5)
    blob = " ".join(f"{e.get('project','')} {e.get('text','')} {' '.join(e.get('skills', []))}" for e in evidence).lower()

    # Check both required_skills and skills from job or job_spec
    raw_req = job.get("required_skills") or job.get("skills") or (state.get("job_spec") or {}).get("required_skills") or []
    required = [s.lower() for s in raw_req]

    matched = [s for s in required if re.search(rf"\b{re.escape(s)}\b", blob)]
    missing = [s for s in required if s not in matched]

    coverage = len(matched) / len(required) if required else (0.5 if evidence else 0.0)
    score = 0.0 if not evidence else round(0.7 * coverage + 0.3 * min(1.0, len(evidence) / 3), 2)
    ids = list(dict.fromkeys(e.get("evidence_id", "") for e in evidence if e.get("evidence_id")))
    report = FitReport(score=score, matched_skills=matched, missing_skills=missing, evidence_ids=ids)
    return {"fit_report": report.model_dump(), "evidence": evidence, "trace": trace}
