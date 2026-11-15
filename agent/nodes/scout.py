"""Scout: find jobs, drop ones already seen, hand ONE complete job into the bid graph.

The marketplace's search only returns summaries (title, skills, budget...), not the description or the screening
questions. The Risk Assessor (scam / prompt-injection scan) and the Analyst work on the description, so the Scout
always loads the full job with get_job before anything else sees it. If get_job fails the node fails (retry, then
dead letter): a job is never analysed on its title alone.
"""
import hashlib

from agent.deps import Deps, call_tool


def fingerprint(job: dict) -> str:
    key = f"{job.get('job_id','')}|{job.get('title','').lower().strip()}|{job.get('client_id','')}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def default_queries(deps: Deps) -> list[str]:
    """Saved searches of this user: the queries configured for their persona (config.yaml -> scout)."""
    scout = getattr(deps, "scout", None) or {}
    persona = getattr(deps, "persona_id", None)
    queries = (scout.get("queries_by_persona") or {}).get(persona) or scout.get("fallback_queries") or ["developer"]
    return [q.strip() for q in queries if q and len(q.strip()) >= 2]       # the server needs >= 2 characters


def full_job(deps: Deps, job: dict, trace: list) -> dict:
    """Search results are summaries. Merge in get_job so the job has its description and screening questions."""
    full = call_tool(trace, "get_job", deps.require_marketplace().get_job, job_id=job["job_id"])
    if isinstance(full, dict) and isinstance(full.get("job"), dict):        # some servers wrap the job
        full = full["job"]
    return {**job, **(full or {})}


def pull_new_jobs(deps: Deps, query: str = "", limit: int | None = None, trace: list | None = None) -> list[dict]:
    """Run the user's searches and keep only unseen jobs (also de-duplicates inside the batch)."""
    trace = trace if trace is not None else []
    market = deps.require_marketplace()
    limit = limit or int((getattr(deps, "scout", None) or {}).get("limit_per_query", 20))
    queries = [query.strip()] if query and len(query.strip()) >= 2 else default_queries(deps)
    fresh, batch = [], set()
    for q in queries:
        for j in call_tool(trace, "search_jobs", market.search_jobs, query=q, limit=limit):
            fp = fingerprint(j)
            if fp in batch or deps.memory.seen(deps.user_id, fp):
                continue
            batch.add(fp)
            fresh.append(j)
    return fresh


def scout_node(state, deps: Deps):
    trace: list = []
    job = state.get("job")
    if job:
        # A job was handed in (the worker or the scheduler already picked it for this thread). The thread id is
        # unique per user+job, so there is no duplicate check here: re-running the node after a crash must not
        # turn a half-finished job into "no_jobs".
        if "description" not in job:
            job = full_job(deps, job, trace)
    else:
        fresh = pull_new_jobs(deps, state.get("query", ""), trace=trace)
        if not fresh:
            return {"status": "no_jobs", "trace": trace}
        job = full_job(deps, fresh[0], trace)
    deps.memory.mark_seen(deps.user_id, fingerprint(job))
    persona = getattr(deps, "persona_id", None)
    if persona:                                     # retrieval uses the logged-in user's persona
        job = {**job, "persona_id": persona}
    return {"job": job, "trace": trace}
