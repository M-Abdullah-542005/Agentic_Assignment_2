"""Node wrapper: step/token budgets, retries with backoff, timeout, dead-letter, trace. Plus small helpers."""
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable

from langgraph.errors import GraphBubbleUp

from agent.deps import Deps
from agent.marketplace_port import TRACE_ID, clean_trace_id
from agent.state import TERMINAL


def thread_id(config) -> str:
    return ((config or {}).get("configurable") or {}).get("thread_id", "")


def idem_key(thread: str, *parts: str) -> str:
    """Stable idempotency key for a side effect: the same thread + parts always give the same key, so a retry
    after a crash replays the stored result instead of acting twice. Matches the servers' key pattern."""
    digest = hashlib.sha1("|".join([thread, *map(str, parts)]).encode()).hexdigest()[:24]
    return f"gb-{digest}"


def category_of(job: dict) -> str:
    if job.get("category"):
        return str(job["category"]).lower()
    skills = job.get("skills") or []
    return str(skills[0]).lower() if skills else "general"


def guarded(name: str, fn: Callable, deps: Deps, human: bool = False) -> Callable:
    """Wrap fn(state, deps) -> dict as a graph node.

    - already-terminal threads do nothing
    - step or token budget exceeded -> status needs_attention
    - exceptions are retried (bounded, exponential backoff); then the thread goes to dead_letter
    - human nodes (interrupt) are not retried or timed out: waiting for a person is not a failure
    - the run's trace id (from the API request, kept in state) is put in a ContextVar for the node's whole
      execution, so every MCP call it makes carries it as x-trace-id. `thread_id` is added to the state
      the node sees; side-effect keys are derived from it, never from the trace id.
    """
    def node(state, config=None):
        if state.get("status") in TERMINAL:
            return {}
        thread = thread_id(config)
        tid = clean_trace_id(state.get("trace_id")) or thread
        state = {**state, "trace_id": tid, "thread_id": thread}

        def run(s, d):
            token = TRACE_ID.set(tid)
            try:
                return fn(s, d)
            finally:
                TRACE_ID.reset(token)

        if not human:
            if state.get("steps", 0) >= deps.max_steps or state.get("tokens", 0) >= deps.max_tokens:
                return {"status": "needs_attention", "steps": 1,
                        "errors": [f"{name}: step/token budget exceeded "
                                   f"(steps={state.get('steps', 0)}, tokens={state.get('tokens', 0)})"]}
        t0 = time.time()
        if human:
            out = run(state, deps)
        else:
            out, err = None, ""
            for attempt in range(deps.retries + 1):
                try:
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        out = pool.submit(run, state, deps).result(timeout=deps.timeout)
                    break
                except GraphBubbleUp:
                    raise
                except FutureTimeout:
                    err = f"timeout after {deps.timeout}s"
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                time.sleep(deps.backoff * (2 ** attempt))
            if out is None:
                return {"status": "dead_letter", "steps": 1, "errors": [f"{name}: {err}"],
                        "trace": [{"tool": name, "arguments": "", "result": "", "observation": err,
                                   "latency_s": round(time.time() - t0, 4), "trace_id": tid}]}
        out = dict(out)
        trace = out.pop("trace", [])
        trace.append({"tool": f"node:{name}", "arguments": "", "result": ",".join(sorted(out)),
                      "observation": "ok", "latency_s": round(time.time() - t0, 4)})
        out["trace"] = [{**t, "trace_id": tid} for t in trace]
        out["steps"] = 1
        out["tokens"] = max(1, len(str({k: v for k, v in out.items() if k != "trace"})) // 4)
        return out
    return node


def lcfirst(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
