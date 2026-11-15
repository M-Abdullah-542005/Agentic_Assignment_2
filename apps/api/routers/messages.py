from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from auth import current_user
from core import diff, pending, request_resume, tail
from db import get_db
from taskqueue import QueueUnavailable, enqueue
from user_runtime import cfg
from schemas import IncomingIn, ApprovalIn
router = APIRouter(prefix="/messages", tags=["messages"])

@router.post("/{cid}/incoming")
def incoming(cid: str, b: IncomingIn, request: Request, user=Depends(current_user)):
    """Queues the conversation graph for the worker (it stops at the interrupt with a draft reply) and streams progress."""
    a, g, c = request.app, request.app.state.conv_graph, cfg(user, "c-" + cid)
    get_db().add_conv(user, cid)
    if not g.get_state(c).values:
        inp = {"conversation_id": cid, "job_id": b.job_id, "new_message": {"id": b.message_id, "text": b.text}}
        try:
            enqueue(a.state.rt, "run_conversation", user, cid, inp, request.state.trace_id, job_id=f"conv:{user}:{cid}:{b.message_id}")
        except QueueUnavailable as e:
            raise HTTPException(503, f"work queue unavailable: {e}")
    return StreamingResponse(tail(g, c), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@router.get("")
def list_convs(request: Request, user=Depends(current_user)):
    g, out = request.app.state.conv_graph, []
    for cid in get_db().convs_of(user):
        st, iv = pending(g, cfg(user, "c-" + cid)); v = st.values
        out.append({"id": cid, "intent": v.get("intent"), "draft": v.get("draft_reply"), "slots": v.get("slots", []),
                    "status": v.get("status") or ("running" if v else "queued"), "pending": iv is not None, "messages": v.get("messages", [])})
    return out

@router.post("/{cid}/approval")
def approve(cid: str, b: ApprovalIn, request: Request, user=Depends(current_user)):
    g, c = request.app.state.conv_graph, cfg(user, "c-" + cid)
    st, _ = pending(g, c)
    if b.action == "reject" and not b.reason: raise HTTPException(422, "reason required")
    p = {"action": b.action, "text": b.text or b.cover_letter, "reason": b.reason}
    if b.action == "edit": p["diff"] = diff(st.values.get("draft_reply", ""), p["text"] or "")
    v = request_resume(request.app, user, "conv", "c-" + cid, p, "approval", request.state.trace_id)
    if v is None: return {"status": "processing", "sent": None}
    return {"status": v.get("status"), "sent": v.get("sent")}
