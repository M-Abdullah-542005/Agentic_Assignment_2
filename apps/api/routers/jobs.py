import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from auth import current_user
from db import get_db
from schemas import JobRow
from taskqueue import QueueUnavailable, enqueue
from user_runtime import cfg

router = APIRouter(prefix="/jobs", tags=["jobs"])

_STREAM_FILE = Path(__file__).resolve().parents[3] / "data" / "jobs" / "stream.json"


def _seed_from_stream(app, user: str, db, limit: int = 15) -> None:
    """Directly seed the user's inbox with jobs from data/jobs/stream.json.

    This is a fallback for when the marketplace MCP is unavailable or returns
    too few results. The jobs are added to the registry and their data is
    written directly into the graph state via the fast-triage path.
    """
    if not _STREAM_FILE.exists():
        return
    try:
        jobs = json.loads(_STREAM_FILE.read_text())
    except Exception:
        return

    # Pick a rotating slice so different users (or restarts) don't always see the same first N jobs.
    import hashlib
    offset = int(hashlib.sha1(user.encode()).hexdigest(), 16) % max(1, len(jobs))
    rotated = jobs[offset:] + jobs[:offset]
    added = 0
    for j in rotated:
        if added >= limit:
            break
        jid = str(j.get("job_id", ""))
        title = str(j.get("title", ""))
        if jid and db.add_job(user, jid, title):
            added += 1


def row(job_id, v, title=""):
    q, f, r = v.get("price_quote") or {}, v.get("fit_report") or {}, v.get("risk_report") or {}
    stage = (v.get("outcome") or {}).get("stage") or ("submitted" if v.get("status") == "submitted" else ("lost" if v.get("status") in ("rejected", "skipped") else "drafted"))
    return JobRow(job_id=job_id, title=(v.get("job_spec") or v.get("job") or {}).get("title") or title or job_id,
                  fit=f.get("score", 0), risk=r.get("score", 0), action=v.get("decision", "pending"),
                  price=q.get("amount"), connects=q.get("connects_cost", 0),
                  status=v.get("status") or ("running" if v else "queued"), outcome_stage=stage)


def request_scout(request: Request, user: str, job_id: str) -> bool:
    """Ask the worker to scout for this user. Returns False if the queue is down."""
    try:
        enqueue(request.app.state.rt, "scout_user", user, request.state.trace_id, job_id=job_id)
        return True
    except QueueUnavailable as e:
        print(f"[GigBid API] queue unavailable: {e}")
        return False


@router.get("", response_model=list[JobRow])
def inbox(request: Request, response: Response, user=Depends(current_user), min_fit: float = 0, action: str | None = None):
    """Reads the user's jobs and their checkpoints. For a fresh user, seeds their initial jobs immediately so they show up."""
    db = get_db()
    jobs = db.jobs_of(user)
    if not jobs:
        try:
            from tasks import scout_user_sync
            scout_user_sync(request.app.state.rt, user, getattr(getattr(request, "state", None), "trace_id", ""))
            jobs = db.jobs_of(user)
        except Exception as e:
            print(f"[GigBid API] Initial scout for user {user} failed: {e}")
        # If still no jobs (marketplace unavailable or no results), seed from local stream.json
        if not jobs:
            try:
                _seed_from_stream(request.app, user, db)
                jobs = db.jobs_of(user)
            except Exception as e:
                print(f"[GigBid API] Stream seed fallback failed: {e}")
            if not jobs and db.claim_seed(user):
                if not request_scout(request, user, f"scout:{user}:seed"):
                    db.release_seed(user)
                    response.headers["X-Queue-Status"] = "unavailable"

    # Fast triage unanalyzed jobs so fit, risk, decision, and quotes appear immediately without waiting
    g = request.app.state.bid_graph
    try:
        from agent.nodes.analyst import analyst_node
        from agent.nodes.decision import decision_node
        from agent.nodes.fit_matcher import fit_node
        from agent.nodes.pricing import pricing_node
        from agent.nodes.risk_assessor import risk_node
        from user_runtime import fetch_job
        rt = request.app.state.runtimes.for_user(user)

        # Build a quick lookup from stream.json for jobs not in marketplace
        _stream_lookup: dict = {}
        if _STREAM_FILE.exists():
            try:
                for sj in json.loads(_STREAM_FILE.read_text()):
                    _stream_lookup[str(sj.get("job_id", ""))] = sj
            except Exception:
                pass

        for j in jobs:
            jid = j["job_id"]
            c = cfg(user, jid)
            if not g.get_state(c).values:
                try:
                    # Try marketplace first; fall back to stream.json data
                    try:
                        full = fetch_job(request.app, user, jid)
                    except Exception:
                        full = _stream_lookup.get(jid)
                    if not full:
                        continue
                    st = {"job": full, "job_id": jid, "query": jid, "trace_id": f"{user}-{jid}"}
                    st.update(risk_node(st, rt.deps))
                    st.update(fit_node(st, rt.deps))
                    st.update(analyst_node(st, rt.deps))
                    st.update(decision_node(st, rt.deps))
                    st.update(pricing_node(st, rt.deps))
                    dec = st.get("decision")
                    st["status"] = "skipped" if dec == "skip" else "ready"
                    g.update_state(c, st)
                except Exception as ex:
                    print(f"[GigBid API] Triage error for {jid}: {ex}")
    except Exception as e:
        print(f"[GigBid API] Quick triage pass failed: {e}")

    rows = [row(j["job_id"], g.get_state(cfg(user, j["job_id"])).values, j["title"]) for j in jobs]
    rows = [r for r in rows if r.fit >= min_fit and (not action or r.action == action)]
    return sorted(rows, key=lambda r: (-r.fit, r.risk))

@router.post("/refresh", status_code=202)
def refresh(request: Request, user=Depends(current_user)):
    """Scout now (the worker also does it on a schedule)."""
    ok = request_scout(request, user, f"scout:{user}:manual:{request.state.trace_id}")
    return {"queued": ok}

@router.get("/{job_id}")
def detail(job_id: str, request: Request, user=Depends(current_user)):
    v = request.app.state.bid_graph.get_state(cfg(user, job_id)).values      # read only; POST /runs/{id} starts a run
    keys = ["job_spec", "risk_report", "fit_report", "evidence", "bid_decision", "price_quote", "status"]
    out = {k: v.get(k) for k in keys}   # evidence list drives the requirement-to-evidence matrix
    # Fallback: triage stores the raw job under "job" key; analyst_node may not have run yet
    if not out["job_spec"]:
        out["job_spec"] = v.get("job")
    # Fallback: decision_node stores the structured decision as "bid_decision" but also as "decision" (string)
    # Ensure bid_decision is populated; if missing, wrap the string decision value
    if not out["bid_decision"] and v.get("decision"):
        out["bid_decision"] = {"action": v["decision"], "reason": "", "risk_score": None, "fit_score": None}
    out["status"] = out["status"] or ("running" if v else "not_started")
    return out

@router.post("/{job_id}/skip")
def skip(job_id: str, request: Request, user=Depends(current_user)):
    request.app.state.bid_graph.update_state(cfg(user, job_id), {"status": "skipped"})
    return {"ok": True}
