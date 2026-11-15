from collections import Counter
from fastapi import APIRouter, Depends, Request
from auth import current_user
from db import get_db
from user_runtime import cfg
router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("")
def stats(request: Request, user=Depends(current_user)):
    g, rows = request.app.state.bid_graph, []
    for j in get_db().job_ids(user):
        v = g.get_state(cfg(user, j)).values
        if v: rows.append(v)
    sub = [r for r in rows if r.get("status") == "submitted"]
    oc = [(r.get("outcome") or {}).get("stage") for r in sub]
    n = max(len(sub), 1)
    return {"analysed": len(rows), "submitted": len(sub),
            "reply_rate": sum(o in ("replied", "interview", "hired") for o in oc) / n,
            "hire_rate": sum(o == "hired" for o in oc) / n,
            "connects_spent": sum((r.get("price_quote") or {}).get("connects_cost", 0) for r in sub),
            "avg_tokens": sum(r.get("tokens", 0) for r in rows) / max(len(rows), 1),
            "rejections": Counter(r.get("approval_reason") for r in rows if r.get("status") == "rejected").most_common(5)}
