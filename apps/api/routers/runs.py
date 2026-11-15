from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from auth import current_user
from core import tail
from db import get_db
from taskqueue import QueueUnavailable, enqueue
from user_runtime import cfg
router = APIRouter(prefix="/runs", tags=["runs"])
H = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

@router.post("/{job_id}")
def start(job_id: str, request: Request, user=Depends(current_user)):
    """Queue the run for the worker and stream its progress. The API does not run the graph. Starting a job that
    already has a thread (running, waiting for approval, finished) queues nothing: it just follows that thread,
    so re-posting can never restart a graph that is paused at approval."""
    a, g, c = request.app, request.app.state.bid_graph, cfg(user, job_id)
    get_db().add_job(user, job_id)
    if not g.get_state(c).values:
        try:
            enqueue(a.state.rt, "run_job", user, job_id, request.state.trace_id, job_id=f"run:{user}:{job_id}")
        except QueueUnavailable as e:
            raise HTTPException(503, f"work queue unavailable: {e}")
    return StreamingResponse(tail(g, c), media_type="text/event-stream", headers={**H, "X-Trace-Id": request.state.trace_id})

@router.get("/{job_id}/resume")
def attach(job_id: str, request: Request, user=Depends(current_user)):
    """Re-attach after refresh: replays the checkpoint history as events, then follows the thread."""
    return StreamingResponse(tail(request.app.state.bid_graph, cfg(user, job_id)), media_type="text/event-stream", headers=H)
