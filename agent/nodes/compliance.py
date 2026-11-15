"""Compliance Guard: no contact info, no off-platform contact, no guarantees.

The verdict is decided by the rules below and by nothing else, so a model can never talk a draft past the guard.
A model reads the text a second time only to leave advisory notes for the person who approves (shown in `reason`);
those notes never change `allowed`.
"""
import re

from agent.deps import Deps
from agent.handoffs import ComplianceVerdict
from agent.llm import ask_json

CHECKS = {
    "contains an email address": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "contains a phone number": r"\+?\d[\d\s().-]{8,}\d",
    "contains a link": r"https?://|www\.",
    "asks to contact off-platform": r"whatsapp|telegram|skype|discord|contact me (?:at|on)|reach me (?:at|on)|outside (?:of )?upwork",
    "makes a guarantee": r"\bguarantee[sd]?\b|100% (?:success|satisfaction)|risk[- ]free|\bi promise\b|will definitely",
}
INSTRUCTIONS = (
    "Read the draft (in <untrusted> tags) as a platform compliance reviewer. Note anything the keyword rules could "
    "miss: spelled-out or obfuscated contact details (\"name at gmail dot com\"), hints to move the chat off the "
    "platform, promises of results, claims of experience the draft cannot back. "
    'Reply with JSON only: {"notes": ["short note", ...]} (empty list if the draft is fine).'
)


def check_text(text: str) -> ComplianceVerdict:
    found = [label for label, pat in CHECKS.items() if re.search(pat, text, re.I)]
    reason = "; ".join(found)
    return ComplianceVerdict(allowed=not found, violations=found, reason=reason or "ok")


def compliance_node(state, deps: Deps):
    d = state["proposal_draft"]
    text = "\n".join([d["cover_letter"], *d.get("screening_answers", [])])
    verdict = check_text(text)
    review = ask_json(deps, "compliance", {"instructions": INSTRUCTIONS, "draft": text})
    if isinstance(review, dict) and isinstance(review.get("notes"), list):
        notes = [str(n)[:160] for n in review["notes"][:3] if str(n).strip()]
        if notes:
            verdict = verdict.model_copy(update={"reason": f"{verdict.reason} | model notes (advisory): " + "; ".join(notes)})
    out = {"compliance_verdict": verdict.model_dump()}
    if not verdict.allowed and not state.get("human_edited"):
        out["status"] = "blocked"
    return out
