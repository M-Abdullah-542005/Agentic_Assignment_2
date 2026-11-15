from fastapi import APIRouter, Depends, HTTPException, Request
from auth import current_user
from user_runtime import cfg
from schemas import RegenIn
router = APIRouter(prefix="/proposals", tags=["proposals"])

@router.get("/{job_id}")
def get(job_id: str, request: Request, user=Depends(current_user)):
    v = request.app.state.bid_graph.get_state(cfg(user, job_id)).values
    d = v.get("proposal_draft")
    if not d: raise HTTPException(404, "no draft yet")
    c, k = v.get("critique") or {}, v.get("compliance_verdict") or {}
    comments = [{"by": "critic", "text": t} for t in c.get("issues", [])] + \
               [{"by": "compliance", "text": t} for t in k.get("violations", [])]
    return {"draft": d, "claims": v.get("claim_verification", []), "evidence": v.get("evidence", []),
            "comments": comments, "price": v.get("price_quote"), "dry_run": (v.get("submission") or {}).get("dry_run")}

@router.post("/{job_id}/regenerate")
def regenerate(job_id: str, body: RegenIn, request: Request, user=Depends(current_user)):
    """Rewrite one paragraph via the Writer node, then re-verify; the checkpoint is updated, nothing is sent."""
    app, c = request.app, cfg(user, job_id)
    v = app.state.bid_graph.get_state(c).values
    paras = v["proposal_draft"]["cover_letter"].split("\n\n")
    if body.paragraph >= len(paras): raise HTTPException(422, "no such paragraph")
    from agent.nodes.writer import writer_node
    deps = app.state.runtimes.for_user(user).deps          # this user's marketplace, LLM, persona, memory
    out = writer_node({**v, "regen_paragraph": body.paragraph}, deps)
    new = out["proposal_draft"]["cover_letter"].split("\n\n")
    paras[body.paragraph] = new[min(body.paragraph, len(new) - 1)]
    d = {**v["proposal_draft"], "cover_letter": "\n\n".join(paras)}
    app.state.bid_graph.update_state(c, {"proposal_draft": d})
    return {"draft": d}
