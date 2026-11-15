# GigBid tool catalogue

Every capability the agents use is an MCP tool. Agents never touch files, databases or marketplaces directly.
Annotations are `readOnlyHint / destructiveHint / idempotentHint` (`openWorldHint` is true for anything that leaves the machine).
**Auth**: stdio = trusted local process (Claude Desktop / Cursor); Streamable HTTP = `Authorization: Bearer <token>` (tokens from `GIGBID_API_TOKENS`, stored as SHA-256, compared in constant time; the server refuses to start without them).
All servers share: per-user token-bucket rate limit, hash-chained append-only audit log (user, tool, arguments, result, trace id), and an `idempotency_key` + `dry_run` pair on every write (writes default to `dry_run=true`).

| Name | Server | Read/Write | Annotations (RO / Destr / Idem) | Auth | Rationale |
|---|---|---|---|---|---|
| `search_evidence` | portfolio | Read | T / F / T | stdio trusted, HTTP bearer | Grounding: BM25 + skill match over evidence; returns type, source, date, outcome, skills. |
| `get_case_study` | portfolio | Read | T / F / T | same | Full case-study text so proposals quote verified facts. `id` is regex-validated (no path traversal). |
| `list_skills` | portfolio | Read | T / F / T | same | Only `verified: true` skills from `profile.json`; stops the agent claiming unverified skills. |
| `portfolio://case/{id}` (resource template) | portfolio | Read | n/a | same | Lets a host load a full case study into context. |
| `portfolio://proposal/{id}` (resource template) | portfolio | Read | n/a | same | Past winning proposals as style/evidence reference. |
| `answer_screening_question`, `draft_grounded_cover_letter` (prompts) | portfolio | n/a | n/a | same | User-selectable workflows that force evidence lookup before writing. |
| `search_jobs` | marketplace | Read | T / F / T | HTTP bearer | Job discovery; limit <= 50, validated filters. |
| `get_job` | marketplace | Read | T / F / T | HTTP bearer | Details, screening questions, Connects cost. |
| `get_client_history` | marketplace | Read | T / F / T | HTTP bearer | Decide if a job is worth Connects (payment verified, hire rate, spend). |
| `get_connects_balance` | marketplace | Read | T / F / T | HTTP bearer | Budget check before any spend. |
| `list_messages` | marketplace | Read | T / F / T | HTTP bearer | Read client threads. |
| `create_proposal_draft` | marketplace | Write | F / F / T | HTTP bearer | Draft only, no Connects spent. Validates job open, bid type, screening answers. |
| `submit_proposal` | marketplace | Write | F / **T** / T | HTTP bearer | Irreversible and spends Connects, hence destructive. `dry_run` shows exact payload and cost; double submit is impossible (idempotency key + status guard). |
| `send_message` | marketplace | Write | F / F / T | HTTP bearer | Additive (cannot be unsent), so not destructive; key prevents duplicates. |
| `get_free_calendar_slots` | workspace | Read | T / F / T | HTTP bearer | Working-hours slots minus busy events and holds (max 14-day window). |
| `create_tentative_interview_hold` | workspace | Write | F / F / T | HTTP bearer | Tentative hold only, no invite sent; conflict-checked under a lock. |
| `create_email_draft` | workspace | Write | F / F / T | HTTP bearer | Draft only, never sends; header injection blocked (no newlines in subject). |
| Upwork MCP (official, third-party) | upwork | Read + draft | per server | OAuth bearer (per user) | Platform-sanctioned API: no scraping, cookies or browser automation. Live mode is read-throttled and draft-only. |
| GitHub MCP (official, third-party) | github | Read | per server | PAT / OAuth | Maintained by GitHub; ingest repos as portfolio evidence with a read-only scope. |

## Third-party trust rationale
- **Upwork MCP**: published by the marketplace itself, so using it keeps us within Upwork's terms; the agent gets read tools plus `create_proposal_draft` only.
- **GitHub MCP**: first-party GitHub server, scoped to read-only repository access.

## Notes
- `search_evidence` `date` is `null` when neither the evidence item nor the case-study front matter (`date: YYYY-MM-DD`) has one; the sample JSON has no dates yet.
- Retries: reuse the **same** `idempotency_key`. Reusing a key with different arguments is rejected.
