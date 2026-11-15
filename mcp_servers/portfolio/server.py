"""GigBid Portfolio MCP server (read-only) built on the official MCP Python SDK.

Run:  python -m mcp_servers.portfolio.server                      # stdio (Claude Desktop / Cursor)
      GIGBID_API_TOKENS=tok:alice python -m mcp_servers.portfolio.server --transport streamable-http
Env:  PORTFOLIO_DATA_DIR (default data/portfolio/ai_ml_engineer), GIGBID_AUDIT_PATH
"""
from __future__ import annotations

import os
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from mcp_servers.common import AuditLog, TokenBucketLimiter, ToolRuntime, serve
from mcp_servers.portfolio.store import EvidenceFilters, NotFound, PortfolioStore, check_id

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceItem(_Out):
    evidence_id: str
    type: str
    title: str
    source: str
    date: str | None = Field(None, description="ISO date if known; many items have none.")
    outcome: str
    skills: list[str]
    matched_skills: list[str] = Field(description="Skills that literally appear in the requirement.")
    score: float


class SearchEvidenceOutput(_Out):
    requirement: str
    count: int
    items: list[EvidenceItem]


class CaseStudy(_Out):
    evidence_id: str
    title: str
    source: str
    date: str | None
    outcome: str
    skills: list[str]
    text: str = Field(description="Full markdown text of case_studies/{id}.md")


class Skill(_Out):
    name: str
    level: str | None = None
    years: float | None = None


class ListSkillsOutput(_Out):
    freelancer_id: str | None
    skills: list[Skill]


def create_server(data_dir: str | None = None, audit_path: str | None = None,
                  limiter: TokenBucketLimiter | None = None) -> FastMCP:
    store = PortfolioStore(data_dir or os.getenv("PORTFOLIO_DATA_DIR", "data/portfolio/ai_ml_engineer"))
    rt = ToolRuntime("portfolio", AuditLog(audit_path or os.getenv("GIGBID_AUDIT_PATH", "var/audit_portfolio.jsonl")),
                     limiter or TokenBucketLimiter(capacity=30, refill_per_sec=5))
    mcp = FastMCP("gigbid-portfolio", stateless_http=True, json_response=True,
                  instructions="Read-only access to a freelancer's verified portfolio evidence. "
                              "Never claim experience that search_evidence/get_case_study do not support.")

    @mcp.tool(title="Search portfolio evidence", annotations=READ_ONLY)
    async def search_evidence(
        requirement: Annotated[str, Field(min_length=3, max_length=1000, description="Job requirement or screening question to ground.")],
        ctx: Context,
        k: Annotated[int, Field(ge=1, le=20, description="Max items to return.")] = 5,
        filters: EvidenceFilters | None = None,
    ) -> SearchEvidenceOutput:
        """Find portfolio evidence (case studies etc.) that supports a requirement.
        Returns type, source, date, outcome and skills per item, best match first."""
        def body(_call):
            try:
                items = store.search(requirement, k, filters)
            except NotFound as exc:
                raise ToolError(str(exc))
            return SearchEvidenceOutput(requirement=requirement, count=len(items), items=items)
        return await rt.run(ctx, "search_evidence", {"requirement": requirement, "k": k,
                            "filters": filters.model_dump() if filters else None}, body)

    @mcp.tool(title="Get case study", annotations=READ_ONLY)
    async def get_case_study(
        id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$", description="evidence_id, e.g. CS-AI_-001")],
        ctx: Context,
    ) -> CaseStudy:
        """Return the full text of case_studies/{id}.md together with its evidence metadata."""
        def body(_call):
            try:
                meta, text = store.read_markdown("case_studies", id)
            except NotFound as exc:
                raise ToolError(str(exc))
            it = store.item(id) or {}
            return CaseStudy(evidence_id=id, title=it.get("title") or meta.get("title", id),
                             source=it.get("source", f"case_studies/{id}.md"), date=it.get("date") or meta.get("date"),
                             outcome=it.get("outcome") or meta.get("outcome", ""), skills=it.get("skills", []), text=text)
        return await rt.run(ctx, "get_case_study", {"id": id}, body)

    @mcp.tool(title="List verified skills", annotations=READ_ONLY)
    async def list_skills(ctx: Context) -> ListSkillsOutput:
        """List every verified skill from profile.json (unverified skills are never returned)."""
        def body(_call):
            try:
                prof, skills = store.profile(), store.verified_skills()
            except NotFound as exc:
                raise ToolError(str(exc))
            return ListSkillsOutput(freelancer_id=prof.get("freelancer_id"),
                                    skills=[Skill(name=s["name"], level=s.get("level"), years=s.get("years")) for s in skills])
        return await rt.run(ctx, "list_skills", {}, body)

    # ---------------------------------------------------------------- resource templates
    @mcp.resource("portfolio://case/{id}", mime_type="text/markdown", title="Case study (full text)")
    def case_resource(id: str) -> str:
        try:
            check_id(id)
            return store.read_markdown("case_studies", id)[1]
        except (NotFound, ValueError) as exc:
            raise ValueError(str(exc))

    @mcp.resource("portfolio://proposal/{id}", mime_type="text/markdown", title="Past proposal (full text)")
    def proposal_resource(id: str) -> str:
        try:
            check_id(id)
            return store.read_markdown("proposals", id)[1]
        except (NotFound, ValueError) as exc:
            raise ValueError(str(exc))

    # ---------------------------------------------------------------- prompt templates
    @mcp.prompt(name="answer_screening_question", title="Answer a screening question from my portfolio",
                description="Grounded answer to a client's screening question, citing evidence ids.")
    def answer_screening_question(question: str, job_title: str = "", max_words: str = "150") -> str:
        job = f' for the job "{job_title}"' if job_title else ""
        return (
            f"Answer this client screening question{job} "
            f"in at most {max_words} words, in first person, plain and specific.\n\n"
            f"Question: {question}\n\n"
            "Process:\n1. Call `search_evidence` with the question as `requirement` (k=5).\n"
            "2. Call `get_case_study` for the 1-2 strongest items to confirm details.\n"
            "3. Call `list_skills` and only name skills that appear there.\n"
            "4. Write the answer using ONLY facts found in those results; quote metrics exactly as written.\n"
            "5. End with `Evidence: <evidence_id>, ...`.\n"
            "If nothing relevant is found, say so and suggest what the freelancer could add instead of inventing experience.")

    @mcp.prompt(name="draft_grounded_cover_letter", title="Draft a grounded cover letter",
                description="Cover letter whose every claim maps to portfolio evidence.")
    def draft_grounded_cover_letter(job_description: str, tone: str = "confident, concise") -> str:
        return (
            f"Draft a cover letter (max 200 words, tone: {tone}) for this job:\n\n{job_description}\n\n"
            "First call `search_evidence` once per distinct requirement in the job, then `list_skills`. "
            "Every claim must be traceable to an evidence item; list the ids used after the letter. "
            "Do not promise anything the evidence does not show.")

    return mcp


def main() -> None:
    serve(create_server(), "GigBid portfolio MCP server (read-only)", 8001)


if __name__ == "__main__":
    main()
