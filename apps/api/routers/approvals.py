from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from auth import current_user
from core import diff, pending, request_resume
from db import get_db
from user_runtime import cfg
from schemas import ApprovalIn
router = APIRouter(prefix="/approvals", tags=["approvals"])

@router.get("")
def list_pending(request: Request, user=Depends(current_user)):
    """Survives page refresh and API restarts: pending approvals come from the checkpointer and the jobs table."""
    g, out = request.app.state.bid_graph, []
    for jid in get_db().job_ids(user):
        _, iv = pending(g, cfg(user, jid))
        if iv is not None: out.append({"job_id": jid, "payload": iv})
    return out

@router.post("/{job_id}")
def decide(job_id: str, body: ApprovalIn, request: Request, user=Depends(current_user)):
    """The decision is handed to the worker, which resumes the graph (and submits) exactly once."""
    g, c, tid = request.app.state.bid_graph, cfg(user, job_id), request.state.trace_id
    st, iv = pending(g, c)
    if iv is None: raise HTTPException(409, "nothing pending")
    original = st.values["proposal_draft"]["cover_letter"]
    if body.action == "reject":
        if not body.reason: raise HTTPException(422, "reason required")
        v = request_resume(request.app, user, "bid", job_id, {"action": "reject", "reason": body.reason}, "approval", tid)
    elif body.action == "edit":
        letter = body.cover_letter or body.text                 # ONE key for the graph: cover_letter
        if not letter: raise HTTPException(422, "cover_letter required")
        payload = {"action": "edit", "cover_letter": letter}
        if body.screening_answers is not None: payload["screening_answers"] = body.screening_answers
        v = request_resume(request.app, user, "bid", job_id, payload, "approval", tid)   # graph re-runs Compliance
        d = diff(original, letter)    # style memory learns from the edit inside the approval node, never from raw text
        if v is None: return JSONResponse({"status": "processing", "diff": d}, status_code=202)
        return {"status": v.get("status"), "diff": d, "compliance": v.get("compliance_verdict"), "pending_again": True}
    else:
        v = request_resume(request.app, user, "bid", job_id, {"action": "approve"}, "approval", tid)
    if v is None: return JSONResponse({"status": "processing"}, status_code=202)
    return {"status": v.get("status"), "submission": v.get("submission")}
