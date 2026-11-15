"""MarketplacePort: the single interface the agent graph uses for any marketplace.

Both adapters talk to marketplaces ONLY through MCP tools (MultiServerMCPClient over Streamable HTTP).
Switch with ``marketplace.mode`` in agent/config.yaml via :func:`load_port`.

Live (Upwork) mode rules enforced here: reads are throttled, proposals can only be created as DRAFTS,
``submit_proposal``/``send_message`` raise ``LiveModeRestriction`` (the user confirms on Upwork), and
nothing browser-based is used. Token acquisition (OAuth) lives outside this module: pass the access
token through the env var named in config.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import os
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

import yaml


class MarketplaceUnavailable(RuntimeError):
    """Marketplace MCP server is down/slow. The UI shows degraded mode and queues the work."""


class MarketplaceToolError(RuntimeError):
    """The tool ran but rejected the request (validation, insufficient Connects, ...). Not retried."""


class LiveModeRestriction(RuntimeError):
    """Operation is not permitted against the live marketplace."""


# ---------------------------------------------------------------- sync bridge
# The adapters are async (MCP client), but the graph nodes are plain sync functions that run in worker
# threads. run_sync() runs a coroutine on one shared background event loop, so a node can call the port
# from any thread (even a thread that already has its own event loop) and get the real result back.
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True, name="gigbid-mcp-loop").start()
        return _loop


# One trace id flows UI request -> API -> worker -> graph state -> this header -> MCP server -> audit log (E3).
# It lives in a ContextVar: guarded() sets it around every node, tasks set it around their own calls, and
# run_sync() copies it into the coroutine that runs on the shared event-loop thread.
TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar("gigbid_trace_id", default="")


def clean_trace_id(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9_.:\-]", "", value or "")[:64]


@contextlib.contextmanager
def trace_context(trace_id: str | None):
    token = TRACE_ID.set(clean_trace_id(trace_id))
    try:
        yield
    finally:
        TRACE_ID.reset(token)


def run_sync(coro, timeout: float | None = None):
    tid = TRACE_ID.get()

    async def _with_trace():
        TRACE_ID.set(tid)
        return await coro

    fut = asyncio.run_coroutine_threadsafe(_with_trace(), _get_loop())
    try:
        return fut.result(timeout)
    except BaseException:
        fut.cancel()
        raise


def _items(out: Any, key: str) -> list[dict[str, Any]]:
    """MCP servers return a list, or a dict that wraps the list under 'key', 'items' or 'result'."""
    return out if isinstance(out, list) else out.get(key) or out.get("items") or out.get("result") or []


def proposal_id_of(draft: dict[str, Any]) -> str | None:
    return (draft.get("resource_id") or draft.get("proposal_id") or draft.get("id")      # server: resource_id
            or (draft.get("proposal") or {}).get("id"))


NO_ANSWER = "I would be glad to go into this on a call."


def build_draft_payload(draft: dict[str, Any], price: dict[str, Any], job: dict[str, Any] | None = None) -> dict[str, Any]:
    """Agent objects -> the fields the server's create_proposal_draft takes:
    job_id, cover_letter, bid_type, bid_amount, answers = [{"question_id", "answer"}].

    * bid_type must equal the job's budget_type (the server rejects a mismatch).
    * every screening question of the job gets one answer (required ones must not be missing);
      answers for questions the job does not have are dropped (the server rejects unknown ids).
    """
    job = job or {}
    given = list(draft.get("screening_answers") or [])
    answers = []
    for i, q in enumerate(job.get("screening_questions") or []):
        qid = (q.get("question_id") or q.get("id")) if isinstance(q, dict) else None
        if not qid:
            continue
        text = str(given[i]).strip() if i < len(given) and str(given[i]).strip() else NO_ANSWER
        answers.append({"question_id": str(qid)[:32], "answer": text[:2000]})
    kind = str(job.get("budget_type") or job.get("job_type") or job.get("bid_type") or job.get("type") or "fixed").lower()
    return {"job_id": draft["job_id"], "cover_letter": draft["cover_letter"],
            "bid_type": "hourly" if "hour" in kind else "fixed",
            "bid_amount": price["amount"], "answers": answers}


class MarketplacePort(Protocol):
    async def search_jobs(self, query: str, limit: int = 20) -> list[dict[str, Any]]: ...
    async def get_job(self, job_id: str) -> dict[str, Any]: ...
    async def get_client_history(self, client_id: str) -> dict[str, Any]: ...
    async def get_connects_balance(self) -> dict[str, Any]: ...
    async def create_proposal_draft(self, payload: dict[str, Any], idempotency_key: str, dry_run: bool = False) -> dict[str, Any]: ...
    async def list_messages(self, thread_id: str | None = None, job_id: str | None = None) -> list[dict[str, Any]]: ...
    async def submit_proposal(self, proposal_id: str, idempotency_key: str, dry_run: bool = True) -> dict[str, Any]: ...
    async def send_message(self, thread_id: str, body: str, idempotency_key: str, dry_run: bool = True) -> dict[str, Any]: ...


ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


TRANSIENT = ("rate limit", "marketplace_unavailable")     # tool errors worth a bounded retry (rate limit, 5xx)


class McpToolCaller:
    """Calls MCP tools through MultiServerMCPClient with an allow-list, timeout and bounded retries.

    Every call opens its own short session, so the current trace id can be sent as the x-trace-id header
    (the MCP servers put it into their audit log). Pass ``client=`` to inject a fake in tests."""

    def __init__(self, server_name: str, url: str, token: str | None, allowed_tools: list[str],
                 timeout: float = 10.0, max_retries: int = 3, backoff_base: float = 0.5, client: Any = None):
        self.client, self.server = client, server_name
        self._conn = {"transport": "streamable_http", "url": url}
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.allowed, self.timeout, self.retries, self.backoff = set(allowed_tools), timeout, max_retries, backoff_base

    def _client_for_call(self) -> Any:
        if self.client is not None:
            return self.client
        from langchain_mcp_adapters.client import MultiServerMCPClient
        headers = dict(self._headers)
        if tid := TRACE_ID.get():
            headers["x-trace-id"] = tid
        return MultiServerMCPClient({self.server: {**self._conn, "headers": headers}})

    async def _once(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        async with self._client_for_call().session(self.server) as session:
            res = await asyncio.wait_for(session.call_tool(name, args), self.timeout)
        text = next((c.text for c in res.content if getattr(c, "text", None)), "")
        if res.isError:
            raise MarketplaceToolError(text or f"{name} failed")
        if res.structuredContent is not None:
            return res.structuredContent
        return json.loads(text) if text else {}

    async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in self.allowed:
            raise LiveModeRestriction(f"tool '{name}' is not in this agent's allow-list")
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                return await self._once(name, args)
            except MarketplaceToolError as exc:
                if any(t in str(exc).lower() for t in TRANSIENT) and attempt < self.retries - 1:
                    last = exc                      # transient: back off and retry
                else:
                    raise
            except LiveModeRestriction:
                raise
            except Exception as exc:                # transport failures (timeout, connection, 401 from the server, ...)
                last = exc
            await asyncio.sleep(self.backoff * (2 ** attempt) + random.uniform(0, self.backoff / 2))
        raise MarketplaceUnavailable(f"{name} failed after {self.retries} attempts: {last}")


class GigSimAdapter:
    """MarketplacePort over our marketplace MCP server (GigSim or local mock)."""

    def __init__(self, call: ToolCaller):
        self._call = call

    async def search_jobs(self, query, limit=20):
        query = (query or "").strip()
        if len(query) < 2:                    # the server's schema says min_length=2: say it here, clearly
            raise MarketplaceToolError("search_jobs needs a query of at least 2 characters")
        return _items(await self._call("search_jobs", {"query": query, "limit": max(1, min(int(limit), 50))}), "jobs")

    async def get_job(self, job_id):
        return await self._call("get_job", {"job_id": job_id})

    async def get_client_history(self, client_id):
        return await self._call("get_client_history", {"client_id": client_id})

    async def get_connects_balance(self):
        return await self._call("get_connects_balance", {})

    async def list_messages(self, thread_id=None, job_id=None):
        args = {k: v for k, v in (("thread_id", thread_id), ("job_id", job_id)) if v}
        return _items(await self._call("list_messages", args), "threads")

    async def create_proposal_draft(self, payload, idempotency_key, dry_run=False):
        return await self._call("create_proposal_draft", {**payload, "idempotency_key": idempotency_key, "dry_run": dry_run})

    async def submit_proposal(self, proposal_id, idempotency_key, dry_run=True):
        return await self._call("submit_proposal", {"proposal_id": proposal_id, "idempotency_key": idempotency_key, "dry_run": dry_run})

    async def send_message(self, thread_id, body, idempotency_key, dry_run=True):
        return await self._call("send_message", {"thread_id": thread_id, "body": body, "idempotency_key": idempotency_key, "dry_run": dry_run})


class UpworkLiveAdapter:
    """MarketplacePort over the official Upwork MCP server. Drafts only; reads throttled."""

    def __init__(self, call: ToolCaller, tool_map: dict[str, str], min_interval: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self._call, self.map, self.min_interval, self._clock = call, tool_map, min_interval, clock
        self._last: dict[str, tuple[float, Any]] = {}

    async def _read(self, op: str, args: dict[str, Any]) -> Any:
        """Serve repeated identical reads from cache inside the throttle window (no scraping-like polling)."""
        key, now = f"{op}:{json.dumps(args, sort_keys=True)}", self._clock()
        if key in self._last and now - self._last[key][0] < self.min_interval:
            return self._last[key][1]
        out = await self._call(self.map[op], args)
        self._last[key] = (now, out)
        return out

    async def search_jobs(self, query, limit=20):
        return _items(await self._read("search_jobs", {"query": query}), "jobs")[:limit]

    async def get_job(self, job_id):
        return await self._read("get_job", {"job_id": job_id})

    async def get_client_history(self, client_id):
        return await self._read("get_client_history", {"client_id": client_id})

    async def get_connects_balance(self):
        return await self._read("get_connects_balance", {})

    async def list_messages(self, thread_id=None, job_id=None):
        args = {k: v for k, v in (("thread_id", thread_id), ("job_id", job_id)) if v}
        return _items(await self._read("list_messages", args), "threads")

    async def create_proposal_draft(self, payload, idempotency_key, dry_run=False):
        # Draft only. The user approves in GigBid, then confirms the draft on Upwork themselves.
        if dry_run:   # preview before approval: show what would be created, touch nothing on Upwork
            return {"status": "preview", "dry_run": True, **payload}
        return await self._call(self.map["create_proposal_draft"], {**payload, "idempotency_key": idempotency_key})

    async def submit_proposal(self, proposal_id, idempotency_key, dry_run=True):
        raise LiveModeRestriction("live mode creates drafts only; the user confirms/submits on Upwork")

    async def send_message(self, thread_id, body, idempotency_key, dry_run=True):
        raise LiveModeRestriction("live mode never sends messages; draft in GigBid and send on Upwork")


class SyncMarketplace:
    """Sync view of any MarketplacePort, used by the graph nodes (they run in plain threads).
    Same method names and arguments as the port; every async method is run with run_sync()."""

    def __init__(self, port: MarketplacePort):
        self._port = port

    def __getattr__(self, name: str):
        fn = getattr(self._port, name)
        if not asyncio.iscoroutinefunction(fn):
            return fn
        return lambda *args, **kwargs: run_sync(fn(*args, **kwargs))


async def verify_live_tools(call_client: Any, server_name: str, tool_map: dict[str, str]) -> None:
    """Fail fast if the configured tool_map names tools the Upwork server does not expose."""
    async with call_client.session(server_name) as session:
        have = {t.name for t in (await session.list_tools()).tools}
    if missing := sorted(set(tool_map.values()) - have):
        raise MarketplaceUnavailable(f"Upwork MCP server lacks mapped tools: {missing}; edit upwork.tool_map")


def load_port(config_path: str | os.PathLike[str] = "agent/config.yaml", call_override: ToolCaller | None = None,
              mode: str | None = None, token: str | None = None, sync: bool = False, allow_env_token: bool = True):
    """Build the port for ``mode`` (default: marketplace.mode from config).
    ``token`` is the user's own access token. The shared token in the env var named in config is only a
    development fallback and is used only when ``allow_env_token`` is True; otherwise a user without a token
    gets a ValueError (the caller turns that into "connect your marketplace account").
    ``sync=True`` returns a SyncMarketplace, which is what the graph nodes need."""
    cfg = yaml.safe_load(Path(config_path).read_text())
    mode, cl = mode or cfg["marketplace"]["mode"], cfg.get("client", {})
    if mode not in ("gigsim", "upwork"):
        raise ValueError(f"marketplace.mode must be 'gigsim' or 'upwork', got {mode!r}")
    sect = cfg[mode]
    if not token and allow_env_token and sect.get("token_env"):
        token = os.getenv(sect["token_env"])
    if not token and call_override is None:
        raise ValueError(f"no {mode} access token for this user")
    call = call_override or McpToolCaller(mode, sect["url"], token, sect["allowed_tools"], cl.get("timeout_seconds", 10),
                                          cl.get("max_retries", 3), cl.get("backoff_base_seconds", 0.5))
    if mode == "gigsim":
        port = GigSimAdapter(call)
    else:
        port = UpworkLiveAdapter(call, sect["tool_map"], sect.get("read_min_interval_seconds", 30))
    return SyncMarketplace(port) if sync else port
