"""Pricing Strategist: budget + hourly rate + Connects cost -> PriceQuote."""
from agent.deps import Deps, call_tool
from agent.handoffs import JobSpec, PriceQuote


def connects_available(balance) -> int | None:
    """get_connects_balance() returns a dict like {"balance": 120}. Pull the number out of it."""
    if isinstance(balance, (int, float)):
        return int(balance)
    if isinstance(balance, dict):
        for key in ("balance", "connects", "connects_balance", "available", "available_connects"):
            if isinstance(balance.get(key), (int, float)):
                return int(balance[key])
    return None


def pricing_node(state, deps: Deps):
    job, spec, trace = state["job"], JobSpec(**state["job_spec"]), []
    lo, hi = spec.budget_min, spec.budget_max
    kind = str(job.get("budget_type") or job.get("job_type") or job.get("bid_type") or job.get("type") or "fixed").lower()
    hourly = "hour" in kind                       # an hourly bid is a rate, a fixed bid is a project price
    connects = int(job.get("connects_cost") or job.get("connects_required", 8))
    balance = None
    if deps.marketplace is not None:
        balance = connects_available(call_tool(trace, "get_connects_balance", deps.marketplace.get_connects_balance))
    if hourly:
        rate = float(deps.hourly_rate)
        if lo is not None and hi is not None and hi > 0:
            amount = round(min(max(rate, lo), hi), 2)
            why = f"Hourly job, client range ${lo} - ${hi}/h; your rate ${rate}/h kept inside it (${amount}/h)"
        elif hi:
            amount = round(min(rate, hi), 2)
            why = f"Hourly job, client ceiling ${hi}/h; your rate ${rate}/h capped to ${amount}/h"
        else:
            amount, why = round(rate, 2), f"Hourly job with no stated range; your rate ${rate}/h"
    elif lo is not None and hi is not None and hi > 0:
        amount = round(lo + 0.6 * (hi - lo), 2)
        why = f"Client budget ${lo} - ${hi}; recommended bid at 60% of range (${amount})"
    elif hi:
        amount, why = round(0.9 * hi, 2), f"Client budget ceiling ${hi}; recommended bid at 90% (${amount})"
    else:
        hours = 20
        amount = round(deps.hourly_rate * hours, 2)
        why = f"No fixed budget; estimated {hours}h at ${deps.hourly_rate}/h (${amount})"
    why += f". Connects required: {connects}" + (f" ({balance} available)" if balance is not None else "")
    if balance is not None and balance < connects:
        why += " (WARNING: insufficient connects)"
    weekly = getattr(deps, "connects_budget", None)
    if weekly and connects > weekly:
        why += f" (WARNING: more than your weekly Connects budget of {weekly})"
    q = PriceQuote(amount=amount, connects_cost=connects, connects_balance=balance, rationale=why)
    return {"price_quote": q.model_dump(), "trace": trace}
