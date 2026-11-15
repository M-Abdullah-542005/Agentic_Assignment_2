"""GigBid Marketplace MCP server: read + write tools over GigSim (or the local mock).

Run:  python -m mcp_servers.marketplace.server                       # stdio
      GIGBID_API_TOKENS=tok:alice python -m mcp_servers.marketplace.server --transport streamable-http
Env:  GIGSIM_BASE_URL / GIGSIM_API_KEY (else local mock), GIGSIM_STATE_PATH,
      GIGBID_AUDIT_PATH, GIGBID_IDEMPOTENCY_DB (file, default var/idempotency_marketplace.sqlite), GIGBID_RATE_CAPACITY, GIGBID_RATE_REFILL
Write tools default to dry_run=true: the agent must pass dry_run=false explicitly after approval.
"""
from __future__ import annotations

import os
from typing import Annotated, Any, Awaitable, Callable, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from mcp_servers.common import AuditLog, IdempotencyStore, TokenBucketLimiter, ToolRuntime, serve
from mcp_servers.marketplace.gigsim import CONNECT_PRICE_USD, GigSimBackend, GigSimError, backend_from_env
from mcp_servers.marketplace.schemas import (
    ID_PATTERN, KEY_PATTERN, ClientHistory, ConnectsBalance, JobDetail, ListMessagesOutput,
    SearchJobsOutput, WriteResult)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
# Writes: idempotentHint is true because every write requires an idempotency_key.
DRAFT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
SUBMIT = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)  # spends Connects, irreversible
MESSAGE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)  # additive, but cannot be unsent

JobId = Annotated[str, Field(pattern=ID_PATTERN, description="GigSim job id, e.g. job_001")]
IdemKey = Annotated[str, Field(pattern=KEY_PATTERN, description="Caller-generated unique key (8-128 chars). Reuse the SAME key when retrying.")]
DryRun = Annotated[bool, Field(description="true (default): return exactly what would be sent and its cost, change nothing.")]


class ProposalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,32}$")
    answer: str = Field(min_length=1, max_length=2000)


def _money(connects: int) -> float:
    return round(connects * CONNECT_PRICE_USD, 2)


def create_server(backend: GigSimBackend | None = None, audit_path: str | None = None,
                  idem_db: str | None = None, limiter: TokenBucketLimiter | None = None) -> FastMCP:
    gs = backend or backend_from_env()
    limiter = limiter or TokenBucketLimiter(float(os.getenv("GIGBID_RATE_CAPACITY", 10)),
                                            float(os.getenv("GIGBID_RATE_REFILL", 0.5)))  # 30 req/min sustained
    default_idem = os.getenv("GIGBID_IDEMPOTENCY_DB")
    if not default_idem:
        default_idem = "var/idempotency_marketplace.sqlite" if os.getenv("GIGBID_ENV") else ":memory:"
    rt = ToolRuntime("marketplace", AuditLog(audit_path or os.getenv("GIGBID_AUDIT_PATH", "var/audit_marketplace.jsonl")),
                     limiter, IdempotencyStore(idem_db or default_idem))
    mcp = FastMCP("gigbid-marketplace", stateless_http=True, json_response=True,
                  instructions="Marketplace tools. Write tools default to dry_run=true; show the preview to the user "
                              "and only call again with dry_run=false (same idempotency_key) once approved.")

    async def guarded(awaitable: Awaitable[Any]) -> Any:
        """Translate backend failures into clean ToolErrors (degraded mode is visible to the agent)."""
        try:
            return await awaitable
        except GigSimError as exc:
            raise ToolError(str(exc))
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise ToolError(f"marketplace_unavailable: {exc}")

    # ---------------------------------------------------------------- read tools
    @mcp.tool(title="Search jobs", annotations=READ)
    async def search_jobs(
        query: Annotated[str, Field(min_length=2, max_length=200)], ctx: Context,
        job_type: Literal["fixed", "hourly"] | None = None,
        min_budget: Annotated[float | None, Field(ge=0, le=1_000_000)] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> SearchJobsOutput:
        """Search open jobs by keywords. min_budget compares against the job's maximum budget."""
        async def body(_c):
            jobs = await guarded(gs.search_jobs(query, job_type=job_type, min_budget=min_budget, limit=limit))
            return SearchJobsOutput(total=len(jobs), jobs=jobs)
        return await rt.run(ctx, "search_jobs", dict(query=query, job_type=job_type, min_budget=min_budget, limit=limit), body)

    @mcp.tool(title="Get job", annotations=READ)
    async def get_job(job_id: JobId, ctx: Context) -> JobDetail:
        """Full job details including screening questions and Connects required."""
        async def body(_c):
            return JobDetail(**await guarded(gs.get_job(job_id)))
        return await rt.run(ctx, "get_job", {"job_id": job_id}, body)

    @mcp.tool(title="Get client history", annotations=READ)
    async def get_client_history(client_id: Annotated[str, Field(pattern=ID_PATTERN)], ctx: Context) -> ClientHistory:
        """Client's hiring/payment track record - use it to decide whether a job is worth Connects."""
        async def body(_c):
            return ClientHistory(**await guarded(gs.get_client_history(client_id)))
        return await rt.run(ctx, "get_client_history", {"client_id": client_id}, body)

    @mcp.tool(title="Get Connects balance", annotations=READ)
    async def get_connects_balance(ctx: Context) -> ConnectsBalance:
        """Current Connects balance and price per Connect in USD."""
        async def body(_c):
            return ConnectsBalance(**await guarded(gs.get_connects_balance()))
        return await rt.run(ctx, "get_connects_balance", {}, body)

    @mcp.tool(title="List messages", annotations=READ)
    async def list_messages(
        ctx: Context,
        thread_id: Annotated[str | None, Field(pattern=ID_PATTERN)] = None,
        job_id: Annotated[str | None, Field(pattern=ID_PATTERN)] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Max messages per thread (most recent).")] = 20,
    ) -> ListMessagesOutput:
        """Message threads, optionally filtered by thread or job."""
        async def body(_c):
            return ListMessagesOutput(threads=await guarded(gs.list_messages(thread_id, job_id, limit)))
        return await rt.run(ctx, "list_messages", dict(thread_id=thread_id, job_id=job_id, limit=limit), body)

    # ---------------------------------------------------------------- write tools
    @mcp.tool(title="Create proposal draft", annotations=DRAFT)
    async def create_proposal_draft(
        job_id: JobId,
        cover_letter: Annotated[str, Field(min_length=50, max_length=5000)],
        bid_type: Literal["fixed", "hourly"],
        bid_amount: Annotated[float, Field(gt=0, le=100_000, description="USD: fixed price or hourly rate.")],
        idempotency_key: IdemKey,
        ctx: Context,
        answers: Annotated[list[ProposalAnswer], Field(max_length=10)] = [],
        dry_run: DryRun = True,
    ) -> WriteResult:
        """Create (not submit) a proposal draft. Costs no Connects; Connects are charged by submit_proposal."""
        async def preview() -> dict[str, Any]:
            job = await guarded(gs.get_job(job_id))
            if job["status"] != "open":
                raise ToolError(f"job '{job_id}' is closed")
            if bid_type != job["budget_type"]:
                raise ToolError(f"bid_type '{bid_type}' does not match the job's budget type '{job['budget_type']}'")
            known = {q["question_id"]: q for q in job["screening_questions"]}
            given = [a.question_id for a in answers]
            if len(set(given)) != len(given):
                raise ToolError("duplicate question_id in answers")
            if unknown := sorted(set(given) - set(known)):
                raise ToolError(f"unknown screening question ids: {unknown}")
            if missing := sorted(q for q, v in known.items() if v["required"] and q not in given):
                raise ToolError(f"missing answers for required screening questions: {missing}")
            bal = (await guarded(gs.get_connects_balance()))["balance"]
            n = job["connects_required"]
            return {"action": "create_proposal_draft", "resource_id": None,
                    "would_send": {"job_id": job_id, "cover_letter": cover_letter, "bid_type": bid_type,
                                   "bid_amount": bid_amount, "answers": [a.model_dump() for a in answers]},
                    "cost": {"connects": 0, "usd": 0.0, "balance_before": bal, "balance_after": bal, "sufficient": bal >= n,
                             "note": f"No cost now; submitting will spend {n} Connects (${_money(n)})."}}

        async def commit(pv: dict[str, Any]) -> dict[str, Any]:
            p = await guarded(gs.create_proposal_draft(pv["would_send"], idempotency_key))
            return {**pv, "status": "draft_created", "resource_id": p["proposal_id"]}

        args = dict(job_id=job_id, cover_letter=cover_letter, bid_type=bid_type, bid_amount=bid_amount,
                    answers=[a.model_dump() for a in answers], idempotency_key=idempotency_key, dry_run=dry_run)
        return WriteResult(**await rt.run_write(ctx, "create_proposal_draft", args, idempotency_key=idempotency_key,
                                                dry_run=dry_run, preview=preview, commit=commit))

    @mcp.tool(title="Submit proposal", annotations=SUBMIT)
    async def submit_proposal(
        proposal_id: Annotated[str, Field(pattern=ID_PATTERN)],
        idempotency_key: IdemKey,
        ctx: Context,
        dry_run: DryRun = True,
    ) -> WriteResult:
        """Submit an existing draft. IRREVERSIBLE and spends Connects. Needs explicit user approval
        (call with dry_run=true first and show the preview)."""
        async def preview() -> dict[str, Any]:
            p = await guarded(gs.get_proposal(proposal_id))
            if p["status"] != "draft":
                raise ToolError(f"proposal '{proposal_id}' is already {p['status']}; nothing to submit")
            bal = (await guarded(gs.get_connects_balance()))["balance"]
            n = p["connects_required"]
            if bal < n:
                raise ToolError(f"insufficient Connects: need {n}, have {bal}")
            sent = {k: p[k] for k in ("job_id", "cover_letter", "bid_type", "bid_amount", "answers")}
            return {"action": "submit_proposal", "resource_id": proposal_id,
                    "would_send": {"proposal_id": proposal_id, **sent},
                    "cost": {"connects": n, "usd": _money(n), "balance_before": bal, "balance_after": bal - n, "sufficient": True}}

        async def commit(pv: dict[str, Any]) -> dict[str, Any]:
            await guarded(gs.submit_proposal(proposal_id, idempotency_key))
            return {**pv, "status": "submitted"}

        args = dict(proposal_id=proposal_id, idempotency_key=idempotency_key, dry_run=dry_run)
        return WriteResult(**await rt.run_write(ctx, "submit_proposal", args, idempotency_key=idempotency_key,
                                                dry_run=dry_run, preview=preview, commit=commit))

    @mcp.tool(title="Send message", annotations=MESSAGE)
    async def send_message(
        thread_id: Annotated[str, Field(pattern=ID_PATTERN)],
        body: Annotated[str, Field(min_length=1, max_length=4000)],
        idempotency_key: IdemKey,
        ctx: Context,
        dry_run: DryRun = True,
    ) -> WriteResult:
        """Send a message to a client in an existing thread."""
        async def preview() -> dict[str, Any]:
            threads = await guarded(gs.list_messages(thread_id, None, 1))
            if not threads:
                raise ToolError(f"thread '{thread_id}' not found")
            bal = (await guarded(gs.get_connects_balance()))["balance"]
            return {"action": "send_message", "resource_id": None, "would_send": {"thread_id": thread_id, "body": body},
                    "cost": {"connects": 0, "usd": 0.0, "balance_before": bal, "balance_after": bal, "sufficient": True}}

        async def commit(pv: dict[str, Any]) -> dict[str, Any]:
            m = await guarded(gs.send_message(thread_id, body, idempotency_key))
            return {**pv, "status": "sent", "resource_id": m["message_id"]}

        args = dict(thread_id=thread_id, body=body, idempotency_key=idempotency_key, dry_run=dry_run)
        return WriteResult(**await rt.run_write(ctx, "send_message", args, idempotency_key=idempotency_key,
                                                dry_run=dry_run, preview=preview, commit=commit))

    return mcp


def main() -> None:
    serve(create_server(), "GigBid marketplace MCP server (GigSim)", 8002)


if __name__ == "__main__":
    main()
