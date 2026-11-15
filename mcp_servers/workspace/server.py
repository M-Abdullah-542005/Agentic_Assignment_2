"""GigBid Workspace MCP server: calendar availability, tentative interview holds, email drafts.

A documented local mock of Google Calendar + Gmail (state in a JSON file). Agents only ever see
these MCP tools, so swapping in the real Google APIs later changes nothing for them. Emails are
only ever DRAFTED here, never sent.

Env: WORKSPACE_STATE_PATH (default var/workspace.json), WORKSPACE_BUSY_JSON (optional seed of busy events,
     [{"start": ISO, "end": ISO, "title": str}]), WORKSPACE_WORK_START/END (hours, default 9/17),
     WORKSPACE_TZ (default UTC), GIGBID_AUDIT_PATH, GIGBID_IDEMPOTENCY_DB
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from mcp_servers.common import AuditLog, IdempotencyStore, TokenBucketLimiter, ToolRuntime, serve

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
IdemKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_\-:.]{8,128}$")]
EMAIL = r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Slot(_M):
    start: str
    end: str


class FreeSlots(_M):
    timezone: str
    duration_minutes: int
    slots: list[Slot]


class WorkspaceWrite(_M):
    action: str
    dry_run: bool
    replayed: bool
    idempotency_key: str
    status: str
    resource_id: str | None
    would_create: dict[str, Any]
    trace_id: str


class Calendar:
    def __init__(self, path: str | Path, tz: str, work_start: int, work_end: int, busy: list[dict[str, str]]):
        self.path, self.tz = Path(path), ZoneInfo(tz)
        self.work = (work_start, work_end)
        self.lock = threading.Lock()
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"events": [], "drafts": []}
        self.seed_busy = busy

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=1))

    def busy(self) -> list[tuple[datetime, datetime]]:
        evs = self.seed_busy + [e for e in self.state["events"] if e["status"] != "cancelled"]
        return [(datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"])) for e in evs]

    def conflicts(self, start: datetime, end: datetime) -> bool:
        return any(start < b_end and end > b_start for b_start, b_end in self.busy())

    def free_slots(self, d_from: datetime, d_to: datetime, minutes: int, limit: int) -> list[Slot]:
        step, out = timedelta(minutes=minutes), []
        day = d_from.astimezone(self.tz).date()
        while day <= d_to.astimezone(self.tz).date() and len(out) < limit:
            if day.weekday() < 5:
                cur = datetime.combine(day, dtime(self.work[0]), self.tz)
                close = datetime.combine(day, dtime(self.work[1]), self.tz)
                while cur + step <= close and len(out) < limit:
                    if cur >= d_from and cur + step <= d_to and not self.conflicts(cur, cur + step):
                        out.append(Slot(start=cur.isoformat(), end=(cur + step).isoformat()))
                    cur += step
            day += timedelta(days=1)
        return out


def _parse(ts: str, tz: ZoneInfo) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        raise ToolError(f"'{ts}' is not an ISO-8601 datetime")
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def create_server(state_path: str | None = None, audit_path: str | None = None, idem_db: str | None = None,
                  limiter: TokenBucketLimiter | None = None, busy: list[dict[str, str]] | None = None) -> FastMCP:
    tzname = os.getenv("WORKSPACE_TZ", "UTC")
    if busy is None and os.getenv("WORKSPACE_BUSY_JSON"):
        busy = json.loads(Path(os.environ["WORKSPACE_BUSY_JSON"]).read_text())
    cal = Calendar(state_path or os.getenv("WORKSPACE_STATE_PATH", "var/workspace.json"), tzname,
                   int(os.getenv("WORKSPACE_WORK_START", 9)), int(os.getenv("WORKSPACE_WORK_END", 17)), busy or [])
    rt = ToolRuntime("workspace", AuditLog(audit_path or os.getenv("GIGBID_AUDIT_PATH", "var/audit_workspace.jsonl")),
                     limiter or TokenBucketLimiter(20, 1), IdempotencyStore(idem_db or os.getenv("GIGBID_IDEMPOTENCY_DB", ":memory:")))
    mcp = FastMCP("gigbid-workspace", stateless_http=True, json_response=True,
                  instructions="Calendar + email-draft tools (local mock). Holds are tentative and emails are drafts only.")

    @mcp.tool(title="Get free calendar slots", annotations=READ)
    async def get_free_calendar_slots(
        date_from: Annotated[str, Field(description="ISO-8601 start of search window.")],
        date_to: Annotated[str, Field(description="ISO-8601 end of window (max 14 days after date_from).")],
        ctx: Context,
        duration_minutes: Annotated[int, Field(ge=15, le=240, multiple_of=15)] = 30,
        max_slots: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> FreeSlots:
        """Free slots inside working hours (Mon-Fri), excluding busy events and tentative holds."""
        def body(_c):
            a, b = _parse(date_from, cal.tz), _parse(date_to, cal.tz)
            if b <= a:
                raise ToolError("date_to must be after date_from")
            if b - a > timedelta(days=14):
                raise ToolError("window too large: max 14 days")
            with cal.lock:
                return FreeSlots(timezone=tzname, duration_minutes=duration_minutes,
                                 slots=cal.free_slots(a, b, duration_minutes, max_slots))
        return await rt.run(ctx, "get_free_calendar_slots", dict(date_from=date_from, date_to=date_to,
                            duration_minutes=duration_minutes, max_slots=max_slots), body)

    @mcp.tool(title="Create tentative interview hold", annotations=WRITE)
    async def create_tentative_interview_hold(
        start: str, end: str,
        title: Annotated[str, Field(min_length=3, max_length=200)],
        idempotency_key: IdemKey, ctx: Context,
        attendee_email: Annotated[str | None, Field(pattern=EMAIL)] = None,
        job_id: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")] = None,
        dry_run: bool = True,
    ) -> WorkspaceWrite:
        """Place a TENTATIVE calendar hold (no invite is sent). Fails if it overlaps existing events."""
        args = dict(start=start, end=end, title=title, attendee_email=attendee_email, job_id=job_id,
                    idempotency_key=idempotency_key, dry_run=dry_run)

        async def preview() -> dict[str, Any]:
            s, e = _parse(start, cal.tz), _parse(end, cal.tz)
            if e <= s or e - s > timedelta(hours=4):
                raise ToolError("end must be after start and the hold at most 4 hours")
            with cal.lock:
                if cal.conflicts(s, e):
                    raise ToolError("requested time overlaps an existing event; call get_free_calendar_slots")
            return {"action": "create_tentative_interview_hold", "resource_id": None, "would_create": {
                "start": s.isoformat(), "end": e.isoformat(), "title": title, "status": "tentative",
                "attendee_email": attendee_email, "job_id": job_id}}

        async def commit(pv: dict[str, Any]) -> dict[str, Any]:
            with cal.lock:
                s, e = datetime.fromisoformat(pv["would_create"]["start"]), datetime.fromisoformat(pv["would_create"]["end"])
                if cal.conflicts(s, e):  # re-check under lock
                    raise ToolError("requested time was taken meanwhile")
                ev = {"event_id": f"hold_{uuid.uuid4().hex[:8]}", **pv["would_create"]}
                cal.state["events"].append(ev)
                cal._save()
            return {**pv, "status": "tentative_hold_created", "resource_id": ev["event_id"]}

        return WorkspaceWrite(**await rt.run_write(ctx, "create_tentative_interview_hold", args,
                              idempotency_key=idempotency_key, dry_run=dry_run, preview=preview, commit=commit))

    @mcp.tool(title="Create email draft", annotations=WRITE)
    async def create_email_draft(
        to: Annotated[str, Field(pattern=EMAIL)],
        subject: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")],
        body: Annotated[str, Field(min_length=1, max_length=10000)],
        idempotency_key: IdemKey, ctx: Context,
        job_id: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")] = None,
        dry_run: bool = True,
    ) -> WorkspaceWrite:
        """Save an email DRAFT (never sends). The user reviews and sends it themselves."""
        args = dict(to=to, subject=subject, body=body, job_id=job_id, idempotency_key=idempotency_key, dry_run=dry_run)

        async def preview() -> dict[str, Any]:
            return {"action": "create_email_draft", "resource_id": None,
                    "would_create": {"to": to, "subject": subject, "body": body, "job_id": job_id, "state": "draft"}}

        async def commit(pv: dict[str, Any]) -> dict[str, Any]:
            with cal.lock:
                d = {"draft_id": f"draft_{uuid.uuid4().hex[:8]}", **pv["would_create"]}
                cal.state["drafts"].append(d)
                cal._save()
            return {**pv, "status": "draft_created", "resource_id": d["draft_id"]}

        return WorkspaceWrite(**await rt.run_write(ctx, "create_email_draft", args, idempotency_key=idempotency_key,
                              dry_run=dry_run, preview=preview, commit=commit))

    return mcp


def main() -> None:
    serve(create_server(), "GigBid workspace MCP server (local calendar/email mock)", 8003)


if __name__ == "__main__":
    main()
