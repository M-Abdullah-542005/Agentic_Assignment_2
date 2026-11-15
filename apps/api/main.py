import os, sys, time, uuid
from contextlib import asynccontextmanager
import httpx

sys.path.insert(0, os.path.dirname(__file__))
import bootstrap                                  # noqa: E402  (loads .env, puts agent/ and rag/ on sys.path)
from fastapi import FastAPI, Request             # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from routers import analytics, approvals, auth_router, jobs, memory, messages, proposals, runs  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    import taskqueue
    from user_runtime import GraphProxy

    taskqueue.check_startup()                     # production without Redis is an error
    # One runtime for the whole API: database, checkpointer, THE memory store, per-user graphs.
    # Production without Postgres is fatal (bootstrap raises); development falls back loudly.
    rt = bootstrap.build_runtime()
    app.state.rt, app.state.cfg = rt, rt.cfg
    app.state.store = rt.store                    # the same store the agent writes to: the memory viewer reads it
    app.state.runtimes = rt.registry
    app.state.bid_graph = GraphProxy(rt.registry, "bid_graph")
    app.state.conv_graph = GraphProxy(rt.registry, "conv_graph")
    yield
    rt.close()

app = FastAPI(title="GigBid API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Trace-Id", "X-Queue-Status"])


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """One trace id per request: taken from the UI's X-Trace-Id header (or made here), stored on the request,
    passed to the worker and the graph, sent to every MCP server, and echoed back to the UI."""
    from agent.marketplace_port import clean_trace_id
    tid = clean_trace_id(request.headers.get("x-trace-id")) or uuid.uuid4().hex
    request.state.trace_id = tid
    response = await call_next(request)
    response.headers["X-Trace-Id"] = tid
    return response

for r in (auth_router, jobs, proposals, approvals, messages, runs, analytics, memory): app.include_router(r.router)

@app.get("/health")
def health(request: Request):
    import taskqueue
    rt = request.app.state.rt
    return {"ok": True, "db": rt.db.kind, "checkpointer": type(rt.checkpointer).__name__, "durable": rt.durable,
            "queue": taskqueue.mode()}

@app.get("/health/marketplace")
def marketplace_health():
    c = app.state.cfg; m = c["marketplace"]["mode"]; url = c[m]["url"]
    t = time.time()
    try:
        token = os.environ.get("GIGBID_MARKETPLACE_TOKEN", "tok_test")
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=2)
        # Streamable HTTP MCP responds with 200, 400, 404, 405, or 406 (Not Acceptable without SSE client header)
        if r.status_code in (200, 400, 404, 405, 406):
            return {"ok": True, "latency_ms": int((time.time() - t) * 1000)}
        return {"ok": False, "detail": f"marketplace returned {r.status_code}"}
    except Exception:
        return {"ok": False, "detail": "marketplace unreachable; work is queued"}
