"""Decision node: skip / watch / bid from RiskReport + FitReport. Rules, no model."""
from agent.deps import Deps
from agent.handoffs import BidDecision, FitReport, RiskReport


def decision_node(state, deps: Deps):
    risk, fit = RiskReport(**state["risk_report"]), FitReport(**state["fit_report"])
    if risk.score >= deps.risk_skip:
        action, why = "skip", f"high risk ({risk.score}): {risk.explanation}"
    elif fit.score < deps.fit_low:
        if deps.autonomy.get("auto_skip_low_fit"):
            action, why = "skip", f"low fit ({fit.score}); auto-skip is on"
        else:
            action, why = "watch", f"low fit ({fit.score}); auto-skip is off, left for the user"
    elif fit.score < deps.fit_bid:
        action, why = "watch", f"medium fit ({fit.score}); missing: {', '.join(fit.missing_skills) or 'none'}"
    else:
        action, why = "bid", f"fit {fit.score}, risk {risk.score}"
    d = BidDecision(action=action, reason=why, risk_score=risk.score, fit_score=fit.score)
    status = {"skip": "skipped", "watch": "watching"}.get(action)
    out = {"decision": action, "bid_decision": d.model_dump()}
    if status:
        out["status"] = status
    return out
