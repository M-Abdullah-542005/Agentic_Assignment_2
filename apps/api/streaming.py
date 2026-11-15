import difflib, json, time
from fastapi import HTTPException
from agent.state import TERMINAL

def cfg(user, job): return {"configurable": {"thread_id": f"{user}:{job}"}, "recursion_limit": 60}

def pending(graph, config):
    st = graph.get_state(config)
    return st, interrupt_of(st)

def interrupt_of(st):
    iv = [i.value for t in st.tasks for i in getattr(t, "interrupts", [])]
    return iv[0] if iv else None

def current_ckpt(st):
    return ((st.config or {}).get("configurable") or {}).get("checkpoint_id")

def sse(ev, data): return f"event: {ev}\ndata: {json.dumps(data, default=str)}\n\n"

def _finished(st) -> bool:
    return bool(st.values) and (not st.next or st.values.get("status") in TERMINAL)

def _node_events(history, start):
    """history is oldest first. The nodes that ran between two checkpoints are the `next` of the earlier one."""
    for i in range(max(start, 1), len(history)):
        prev, snap = history[i - 1], history[i]
        vals = snap.values or {}
        for node in prev.next:
            if not node.startswith("__"):
                yield sse("node", {"node": node, "status": vals.get("status"), "tokens": vals.get("tokens", 0),
                                   "trace_id": vals.get("trace_id")})

def tail(graph, config, timeout: float = 600, poll: float = 0.7):
    """SSE generator for the run viewer. The API never runs the graph: the worker does, and this only READS the
    checkpoint and reports each finished node. Replays everything done so far, then follows until the thread
    finishes or waits for a human. Re-attach after a refresh by calling it again. Sync generator -> FastAPI runs
    it in a threadpool."""
    started, last, sent = time.time(), None, 0
    yield sse("queued", {"thread": config["configurable"]["thread_id"]})
    while True:
        try:
            st = graph.get_state(config)
            cid = current_ckpt(st)
            if cid != last:
                last = cid
                history = list(reversed(list(graph.get_state_history(config))))
                yield from _node_events(history, sent)
                sent = len(history)
            if st.values:
                iv = interrupt_of(st)
                if iv is not None:
                    yield sse("interrupt", {"payload": [iv]})
                    yield sse("done", {"status": st.values.get("status"), "next": list(st.next)})
                    return
                if _finished(st):
                    yield sse("done", {"status": st.values.get("status"), "next": list(st.next)})
                    return
        except Exception as e:  # surfaced in the run viewer, never a silent spinner
            yield sse("error", {"message": str(e)[:300]})
            return
        if time.time() - started > timeout:
            yield sse("done", {"status": "running", "next": [], "detail": "still running; re-attach with GET /runs/{id}/resume"})
            return
        time.sleep(poll)

def wait_for_settle(graph, config, prior_ckpt, timeout: float = 25.0, poll: float = 0.4):
    """After a resume was handed to the worker: wait (bounded) until the thread moved past `prior_ckpt` and is
    finished or waiting for a human again. Returns the state values, or None if the worker is still busy."""
    end = time.time() + timeout
    while time.time() < end:
        st = graph.get_state(config)
        if current_ckpt(st) != prior_ckpt and (_finished(st) or interrupt_of(st) is not None):
            return st.values
        time.sleep(poll)
    return None

def diff(a: str, b: str) -> str:
    return "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), "draft", "edited", lineterm=""))

def request_resume(app, user, kind, key, payload, expect_node, trace_id):
    """Exactly-once resume. The decision is handed to the worker, keyed by the checkpoint the thread is paused at:
    a second click, a retry or a restart of the API cannot resume the same pause twice (arq drops the duplicate
    job id, and the worker re-checks the checkpoint before it resumes). Returns the new state values, or None if
    the worker has not finished yet (the UI then polls /approvals or the job)."""
    from taskqueue import QueueUnavailable, enqueue
    from user_runtime import cfg as user_cfg
    graph = app.state.bid_graph if kind == "bid" else app.state.conv_graph
    config = user_cfg(user, key)
    st, iv = pending(graph, config)
    if iv is None or expect_node not in st.next:
        raise HTTPException(409, "no pending approval (already resolved?)")
    ckpt = current_ckpt(st)
    try:
        enqueue(app.state.rt, "resume_job", user, kind, key, payload, ckpt, trace_id, job_id=f"resume:{user}:{key}:{ckpt}")
    except QueueUnavailable as e:
        raise HTTPException(503, f"work queue unavailable: {e}")
    return wait_for_settle(graph, config, ckpt)
