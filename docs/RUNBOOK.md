# GigBid runbook

## One-Command Setup

```bash
make install   # one-time: creates .venv + npm install
make dev       # starts all services
```

## 1. Processes
| Process | Command | Notes |
|---|---|---|
| API | `uvicorn api.main:app --host 0.0.0.0 --port 8000` | SSE endpoint streams node events |
| Worker | `arq worker.WorkerSettings` | scheduled scouting, resumed approvals, retries |
| Marketplace MCP (GigSim) | `python -m mcp_servers.marketplace` (HTTP on :8002) | requires `GIGBID_MARKETPLACE_TOKEN` |
| Portfolio / workspace MCP | `python -m mcp_servers.portfolio`, `python -m mcp_servers.workspace` | refuse to start without `GIGBID_API_TOKENS` on HTTP |
| Frontend | `npm run start` in `apps/web/` | |

(Adjust module paths to your repo if they differ.)

## 2. Schedules
| Job | Interval | Why |
|---|---|---|
| Scout saved searches | every 30 min (never below the 30 s per-operation floor; live mode reads at most that often) | stays well inside platform expectations |
| Connects balance check | before every bid and once daily | budget guard |
| Pending-approval reminder | every 2 h while a draft waits | approvals should not rot |
| Message poll (open proposals) | every 15 min | client replies |
| Checkpoint / audit-log compaction | daily, off-peak | keep stores small |
| RAG incremental re-index | on evidence change (including X3 registration) | no full rebuild |

## 3. Rate limits and retries
* Client: 10 s timeout, 3 retries, exponential backoff 0.5 s base with jitter. Write retries reuse the **same** `idempotency_key`; reusing a key with different arguments is rejected by the server.
* Servers: per-user token bucket. A `429`-style tool error means wait, not retry harder. Scouting skips the tick and logs it.
* LLM provider limits: the worker retries with backoff, then marks the node `degraded` and surfaces it in the UI; no silent fallback to a cheaper model for compliance or verifier nodes.

## 4. Degraded modes
| Symptom | Behaviour | Recovery |
|---|---|---|
| Marketplace MCP down | banner, scouting paused, drafting continues | restart server; worker resumes at next tick; check audit log for in-flight writes |
| Upwork token expired | live mode switches to read-only error state; GigSim unaffected | re-run OAuth; token lives in the per-user encrypted store |
| LLM provider outage | nodes retry, then job parks as `degraded` (no proposal produced) | resume parked jobs: `POST /jobs/{id}/resume` |
| Vector/dense index missing | retriever falls back to lexical variant; verifier still runs | rebuild index (`python -m rag.ingest --rebuild`) |
| Worker crash mid-submit | job stays `approved`; restart replays `submit` with the same key | verify via audit log that exactly one `submit_proposal` succeeded |
| Redis lost | queued jobs lost, checkpoints (separate store) intact | restart; scout re-enqueues; approvals pending in the UI are unaffected |

## 5. Disaster recovery
* **Back up**: checkpoint DB, outcome/style memory, audit log, `data/personas/`, `var/rag_index` (rebuildable). Daily snapshot, 14-day retention.
* **Restore order**: secrets and tokens, persona data, checkpoint DB, memory stores, then start MCP servers, API, worker.
* **Verify after restore**: `python scripts/check_mcp_contracts.py`, open the reviewer persona in the UI, run one GigSim job to the approval gate.
* **Audit log integrity**: replay the hash chain; a break means tampering or a partial restore, stop and investigate before any live writes.
* **Secrets compromise**: rotate `GIGBID_API_TOKENS`, `GIGBID_MARKETPLACE_TOKEN`, the Upwork OAuth tokens and the LLM key; none are committed (CI fails on a secrets scan if enabled).

## 6. Go-live checklist (GigSim to live)
1. Set `marketplace.mode: upwork`; run `list_tools` against the server and confirm every `tool_map` entry exists (startup fails fast otherwise).
2. Confirm the allow-list has no `submit_proposal`, no `send_message`.
3. Keep every send human-approved and confirmed on the platform. Never commit live client data.

## Troubleshooting

| Problem | Fix |
|---|---|
| Port already in use | `fuser -k 8000/tcp` (replace port number as needed) |
| Python import errors | Ensure `PYTHONPATH=apps/api:.` is set before running |
| Next.js build errors | `cd apps/web && npm install` then retry |
| SQLite vs Postgres | Set `GIGBID_SQLITE_PATH` for SQLite or `DATABASE_URL` for Postgres in `.env` |
