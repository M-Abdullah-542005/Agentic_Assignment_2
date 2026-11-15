# ADR 001 — LangGraph over AutoGen / CrewAI for the Bid Graph

**Status:** Accepted  
**Date:** 2026-11-15  
**Deciders:** GigBid core team

---

## Context

GigBid requires a multi-step agent pipeline that:

1. Runs multiple nodes in parallel (analyst, risk, fit)
2. Loops bounded revision cycles (writer ↔ critic, ≤ 2 revisions)
3. Pauses for human approval before any proposal is submitted — and must survive process restarts while paused
4. Streams intermediate events to the UI over SSE in real time
5. Produces an auditable trace of every state transition for compliance review

Three frameworks were evaluated: **LangGraph**, **AutoGen** (v0.4+), and **CrewAI**.

---

## Decision

We chose **LangGraph**.

---

## Reasons

### 1. Persistent checkpointing (human-in-the-loop)

LangGraph's `SqliteSaver` / `PostgresSaver` checkpointers write the full graph state after every node execution. When the process restarts, any job paused at the `approval` node is fully recoverable — the graph resumes from the exact checkpoint without re-running upstream nodes. AutoGen and CrewAI both lack first-class persistent checkpointing; building it ourselves would add significant complexity and risk.

### 2. Native streaming (SSE)

LangGraph exposes `astream_events()` which yields typed events (`on_chat_model_stream`, `on_tool_end`, etc.) as they happen. The FastAPI SSE endpoint forwards these directly to the Next.js UI with no additional polling layer. AutoGen's streaming story requires wrapping callbacks; CrewAI's is not production-ready for per-token streaming.

### 3. Explicit state graph (audit trail)

The graph is declared as a typed `StateGraph` with named nodes and edges. Every edge transition is recorded in the checkpoint, giving a complete audit trail: `scout → analyst | risk | fit → decide → pricing → writer → verifier → critic → compliance → approval → submit`. This satisfies the hard rule that every proposal is traceable. With AutoGen's conversational model, reconstructing which agent made which decision requires parsing message history.

### 4. Interrupt / approval semantics built-in

`interrupt_before=["submit"]` halts execution at the approval node and serialises the pending state. The API surfaces this to the UI as `status: awaiting_approval`. When the user approves (or edits), `graph.update_state()` injects the decision and `graph.invoke(None, config)` resumes — exactly-once, using the same idempotency key already in state. This pattern required no custom code; it is the documented human-in-the-loop flow.

---

## Alternatives Considered

| Framework | Why rejected |
|---|---|
| **AutoGen v0.4** | No persistent checkpointing; streaming requires bespoke callback wiring; graph topology is implicit in conversation history, not declared |
| **CrewAI** | No production-grade checkpointing; approval/interrupt pattern unsupported; per-token streaming immature; less flexible for custom parallel fan-out |

---

## Consequences

- The bid graph is fully expressed as a `StateGraph`; adding or removing nodes is a one-line change.
- Human approval survives crashes and deployments; the reviewer can close their browser and return later.
- Streaming requires the FastAPI server to stay alive for the duration of the SSE connection; the Arq worker handles background completion independently.
- LangGraph's Python-only SDK means the graph cannot run directly in TypeScript; the Next.js UI communicates exclusively via REST + SSE.
