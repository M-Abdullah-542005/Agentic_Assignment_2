"""Risk Assessor: client history, scam signals, unpaid trials, prompt injection -> RiskReport.
Deterministic on purpose: a model reading a hostile post must not be able to lower its own risk score."""
import re

from agent.deps import Deps, call_tool
from agent.handoffs import RiskReport

RULES = {
    "scam: off-platform payment or contact": (re.compile(r"telegram|whatsapp|skype|pay\s+(?:me\s+)?outside|outside\s+(?:of\s+)?upwork|gift\s*card|crypto|wire transfer|western union|send money|pay upfront|upfront (?:fee|payment)", re.I), 0.7),
    "unpaid trial work": (re.compile(r"free sample|unpaid (?:test|trial)|trial (?:task|project) (?:is )?unpaid|work for free|test project.{0,20}unpaid|free of charge first", re.I), 0.35),
    "prompt injection in job post": (re.compile(r"ignore (?:all |any )?(?:previous|prior|above) (?:instructions|rules)|disregard (?:the )?(?:above|previous)|system prompt|you are now|as an ai (?:model|assistant)|reveal your (?:prompt|instructions)", re.I), 0.6),
    "contact details in post": (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{8,}\d", re.I), 0.2),
}


def risk_node(state, deps: Deps):
    job, trace = state["job"], []
    text = f"{job.get('title','')} {job.get('description','')}"
    score, flags = 0.0, []
    for flag, (pattern, weight) in RULES.items():
        if pattern.search(text):
            flags.append(flag)
            score += weight
    hist = {}
    if job.get("client_id") and deps.marketplace is not None:
        hist = call_tool(trace, "get_client_history", deps.marketplace.get_client_history, client_id=job["client_id"]) or {}
    if hist:
        if hist.get("payment_verified") is False:
            flags.append("client payment method not verified"); score += 0.25
        if hist.get("hire_rate") is not None and hist["hire_rate"] < 0.2:
            flags.append("client hire rate below 20%"); score += 0.15
        spent = hist.get("total_spent_usd", hist.get("total_spent"))      # server field is total_spent_usd
        if spent == 0:
            flags.append("client has never spent on the platform"); score += 0.1
        if hist.get("avg_rating") is not None and hist["avg_rating"] < 3.5:
            flags.append("low average rating from freelancers"); score += 0.15
    score = min(1.0, round(score, 2))
    expl = ("Flags: " + "; ".join(flags)) if flags else "No risk signals found."
    return {"risk_report": RiskReport(score=score, flags=flags, explanation=expl).model_dump(), "trace": trace}
