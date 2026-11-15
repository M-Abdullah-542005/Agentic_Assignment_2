"""Proposal Writer: evidence-only draft + screening answers. Style memory is applied at the end."""
import re

from agent.deps import Deps
from agent.handoffs import JobSpec, ProposalDraft
from agent.llm import ask, parse_json
from agent.memory.promotion_rules import apply_style_rules
from agent.nodes.common import category_of, sentences

INSTRUCTIONS = (
    "Write a cover letter and answer the screening questions for the freelancer. Use ONLY facts found in "
    "'evidence'. Do not invent projects, numbers, clients or skills. Address the client's actual project, "
    "keep the letter under 200 words, no links, no email or phone numbers, no guarantees, no contact "
    "outside the platform. Follow 'style_rules' and fix every point in 'critique' if it is not null. "
    "If the evidence does not cover a question, say so honestly. "
    'Reply with JSON only: {"cover_letter": "...", "screening_answers": ["one answer per question, same order"]}.'
)


def _overlap(question: str, e: dict) -> int:
    q = set(re.findall(r"[a-z]{4,}", question.lower()))
    t = set(re.findall(r"[a-z]{4,}", f"{e.get('project','')} {e.get('text','')}".lower()))
    return len(q & t)


def _first(e: dict) -> str:
    s = sentences(e.get("text", ""))
    return s[0].rstrip(".!?") + "." if s else ""


def heuristic_letter(job: dict, spec: JobSpec, evidence: list[dict], winners: dict) -> tuple[str, list[str], list[str]]:
    """Template fallback, used only when no model answered."""
    ev = sorted(evidence, key=lambda e: -winners.get(e.get("evidence_id", ""), 0))[:3]   # outcome memory: prefer winners
    name = job.get("client_name") or "there"
    lines = [f"Hi {name}, I read your post for {spec.title}."]
    for e in ev:
        if _first(e):
            lines.append(f"{e.get('project', 'A past project')}: {_first(e)}")
        if e.get("outcome"):
            lines.append(f"Outcome: {str(e['outcome']).rstrip('.')}.")
    lines.append("Happy to discuss the details and timeline.")
    answers = []
    for q in spec.screening_questions:
        best = max(evidence, key=lambda e: _overlap(q, e), default=None)
        if best and _overlap(q, best) > 0 and _first(best):
            answers.append(_first(best))
        else:
            answers.append("I do not have directly relevant past work for this one, but I am happy to talk it through.")
    return "\n".join(lines), answers, [e.get("evidence_id", "") for e in ev if e.get("evidence_id")]


def writer_node(state, deps: Deps):
    job, spec = state["job"], JobSpec(**state["job_spec"])
    evidence = state.get("evidence", [])
    rules = deps.memory.style_rules(deps.user_id)
    winners = deps.memory.outcome_summary(deps.user_id, category_of(job)).get("winning_evidence", {})
    letter, answers, ids = heuristic_letter(job, spec, evidence, winners)
    text = ask(deps, "writer", {"instructions": INSTRUCTIONS, "job": job, "job_spec": spec.model_dump(),
                                "evidence": evidence, "style_rules": rules, "critique": state.get("critique")})
    if text:
        data = parse_json(text)
        if isinstance(data, dict) and isinstance(data.get("cover_letter"), str) and data["cover_letter"].strip():
            letter = data["cover_letter"].strip()
            llm_answers = data.get("screening_answers")
            if isinstance(llm_answers, list) and len(llm_answers) == len(spec.screening_questions):
                answers = [str(a) for a in llm_answers]
        elif not text.lstrip().startswith("{"):      # the model answered with plain text: treat it as the letter
            letter = text
        ids = [e.get("evidence_id", "") for e in evidence if e.get("evidence_id")]
    letter = apply_style_rules(letter, rules)
    draft = ProposalDraft(job_id=spec.job_id, cover_letter=letter, screening_answers=answers, evidence_ids=ids)
    return {"proposal_draft": draft.model_dump()}
