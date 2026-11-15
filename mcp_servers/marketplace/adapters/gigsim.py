"""GigSim backends.

* ``LocalMockGigSim``  - deterministic in-process simulator (default; used by tests and demos).
* ``HttpGigSim``       - thin httpx client for a running GigSim API. NOTE: the REST routes below
                         follow the common GigSim convention but must be checked against your
                         instance's OpenAPI document (see ROUTES) - adjust there, nowhere else.
Both expose the same async interface and both honour the idempotency key.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

import httpx

CONNECT_PRICE_USD = 0.15


class GigSimError(Exception):
    """Domain error that is safe to show to the model."""


class GigSimBackend(Protocol):
    async def search_jobs(self, query: str, *, job_type: str | None, min_budget: float | None,
                          limit: int) -> list[dict[str, Any]]: ...
    async def get_job(self, job_id: str) -> dict[str, Any]: ...
    async def get_client_history(self, client_id: str) -> dict[str, Any]: ...
    async def get_connects_balance(self) -> dict[str, Any]: ...
    async def list_messages(self, thread_id: str | None, job_id: str | None, limit: int) -> list[dict[str, Any]]: ...
    async def get_proposal(self, proposal_id: str) -> dict[str, Any]: ...
    async def create_proposal_draft(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...
    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> dict[str, Any]: ...
    async def send_message(self, thread_id: str, body: str, idempotency_key: str) -> dict[str, Any]: ...


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _seed() -> dict[str, Any]:
    def job(i, title, desc, skills, client, connects, btype, lo, hi, qs):
        return {"job_id": f"job_{i:03d}", "title": title, "description": desc, "skills": skills, "client_id": client,
                "connects_required": connects, "budget_type": btype, "budget_min": lo, "budget_max": hi,
                "posted_at": f"2026-11-{10 + i % 4:02d}T09:00:00Z", "proposals_count": 3 + i, "status": "open",
                "screening_questions": [{"question_id": f"q{n + 1}", "text": t, "required": True} for n, t in enumerate(qs)]}
    jobs = [
        job(1, "Build a RAG chatbot over our support docs", "Need an engineer to build a retrieval-augmented chatbot using Qdrant and LangGraph, deployed with FastAPI and Docker.",
            ["RAG Systems", "LangGraph", "Qdrant", "FastAPI"], "client_a", 16, "fixed", 1500, 3000,
            ["Describe a RAG system you shipped.", "How do you evaluate answer quality?"]),
        job(2, "Fine-tune an open LLM for legal summaries", "Fine-tuning and serving with vLLM; PyTorch experience required.",
            ["Fine-Tuning", "PyTorch", "vLLM"], "client_b", 20, "hourly", 40, 80, ["Which models have you fine-tuned?"]),
        job(3, "Python data pipeline cleanup", "Refactor ETL jobs, add Docker and tests.", ["Python", "Docker"], "client_c", 8, "fixed", 500, 900, []),
        job(4, "WordPress theme tweaks", "Small CSS changes on a WordPress site.", ["WordPress", "CSS"], "client_c", 4, "fixed", 50, 100, []),
        job(5, "Vector search for e-commerce catalogue", "Implement semantic product search with ChromaDB or Qdrant; Python and FastAPI.",
            ["Vector Databases", "Qdrant", "Python", "FastAPI"], "client_a", 12, "hourly", 35, 60, ["Have you built semantic search before?"]),
    ]
    clients = {
        "client_a": {"client_id": "client_a", "name": "Acme Support Inc.", "payment_verified": True, "total_spent_usd": 48210.5, "hire_rate": 0.72, "avg_rating": 4.9, "reviews_count": 31, "jobs_posted": 44, "member_since": "2021-04-02"},
        "client_b": {"client_id": "client_b", "name": "LexSum Ltd", "payment_verified": True, "total_spent_usd": 9200.0, "hire_rate": 0.4, "avg_rating": 4.5, "reviews_count": 6, "jobs_posted": 15, "member_since": "2024-01-19"},
        "client_c": {"client_id": "client_c", "name": "QuickFix Co", "payment_verified": False, "total_spent_usd": 0.0, "hire_rate": 0.0, "avg_rating": None, "reviews_count": 0, "jobs_posted": 2, "member_since": "2026-10-30"},
    }

    # Load the 290 stream jobs from data/jobs/stream.json
    stream_path = Path(__file__).parents[2] / "data" / "jobs" / "stream.json"
    if stream_path.exists():
        stream_jobs_raw: list[dict[str, Any]] = json.loads(stream_path.read_text())
        for i, sj in enumerate(stream_jobs_raw):
            client_id = f"client_stream_{i % 20}"
            budget = sj.get("budget", {})
            sq_raw = sj.get("screening_questions", [])
            screening_questions = [
                {"question_id": f"q{n + 1}", "text": q, "required": True}
                for n, q in enumerate(sq_raw)
            ]
            converted = {
                "job_id": sj["job_id"],
                "title": sj.get("title", ""),
                "description": sj.get("description", ""),
                "skills": sj.get("required_skills", []),
                "client_id": client_id,
                "connects_required": sj.get("connects_cost", 6),
                "budget_type": "fixed",
                "budget_min": budget.get("min", 0),
                "budget_max": budget.get("max", 0),
                "posted_at": "2026-11-14T09:00:00Z",
                "proposals_count": 2,
                "status": "open",
                "screening_questions": screening_questions,
            }
            jobs.append(converted)
            # Build a synthetic client entry if not already present
            if client_id not in clients:
                ch = sj.get("client_history", {})
                clients[client_id] = {
                    "client_id": client_id,
                    "name": f"Stream Client {i % 20}",
                    "payment_verified": ch.get("total_spent", 0) > 0,
                    "total_spent_usd": float(ch.get("total_spent", 0)),
                    "hire_rate": float(ch.get("hire_rate", 0)),
                    "avg_rating": float(ch.get("rating", 0)) if ch.get("rating") else None,
                    "reviews_count": 0,
                    "jobs_posted": 0,
                    "member_since": "2021-01-01",
                }

    return {"jobs": {j["job_id"]: j for j in jobs}, "clients": clients, "connects": 80, "proposals": {}, "threads": {
        "thr_001": {"thread_id": "thr_001", "job_id": "job_003", "messages": [
            {"message_id": "msg_001", "thread_id": "thr_001", "sender": "client", "body": "Hi, are you available this week?", "sent_at": "2026-11-12T10:00:00Z"}]}},
        "keys": {}}


class LocalMockGigSim:
    def __init__(self, state_path: str | None = None, connects: int | None = None):
        self.state_path = Path(state_path) if state_path else None
        self.s = json.loads(self.state_path.read_text()) if self.state_path and self.state_path.exists() else _seed()
        if connects is not None:
            self.s["connects"] = connects

    def _save(self) -> None:
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.s))

    # ---- reads
    async def search_jobs(self, query, *, job_type=None, min_budget=None, limit=10):
        terms = [t for t in query.lower().split() if len(t) > 1]
        out = []
        for j in self.s["jobs"].values():
            if j["status"] != "open" or (job_type and j["budget_type"] != job_type):
                continue
            if min_budget is not None and (j["budget_max"] or 0) < min_budget:
                continue
            hay = f"{j['title']} {j['description']} {' '.join(j['skills'])}".lower()
            score = sum(hay.count(t) for t in terms)
            if score:
                out.append({**j, "score": float(score)})
        out.sort(key=lambda j: (-j["score"], j["job_id"]))
        return out[:limit]

    async def get_job(self, job_id):
        if job_id not in self.s["jobs"]:
            raise GigSimError(f"job '{job_id}' not found")
        return self.s["jobs"][job_id]

    async def get_client_history(self, client_id):
        if client_id not in self.s["clients"]:
            raise GigSimError(f"client '{client_id}' not found")
        return self.s["clients"][client_id]

    async def get_connects_balance(self):
        return {"balance": self.s["connects"], "connect_price_usd": CONNECT_PRICE_USD, "as_of": _now()}

    async def list_messages(self, thread_id=None, job_id=None, limit=20):
        ts = [t for t in self.s["threads"].values()
              if (not thread_id or t["thread_id"] == thread_id) and (not job_id or t["job_id"] == job_id)]
        if thread_id and not ts:
            raise GigSimError(f"thread '{thread_id}' not found")
        return [{**t, "messages": t["messages"][-limit:]} for t in ts]

    async def get_proposal(self, proposal_id):
        if proposal_id not in self.s["proposals"]:
            raise GigSimError(f"proposal '{proposal_id}' not found")
        return self.s["proposals"][proposal_id]

    # ---- writes (backend-level idempotency mirrors the upstream Idempotency-Key header)
    def _replay(self, kind: str, key: str):
        rid = self.s["keys"].get(f"{kind}:{key}")
        return rid

    async def create_proposal_draft(self, payload, idempotency_key):
        if (rid := self._replay("draft", idempotency_key)):
            return self.s["proposals"][rid]
        job = await self.get_job(payload["job_id"])
        pid = f"prop_{uuid.uuid4().hex[:8]}"
        self.s["proposals"][pid] = {"proposal_id": pid, "status": "draft", "connects_spent": 0, "created_at": _now(),
                                    "connects_required": job["connects_required"], **payload}
        self.s["keys"][f"draft:{idempotency_key}"] = pid
        self._save()
        return self.s["proposals"][pid]

    async def submit_proposal(self, proposal_id, idempotency_key):
        if (rid := self._replay("submit", idempotency_key)):
            return self.s["proposals"][rid]
        p = await self.get_proposal(proposal_id)
        if p["status"] != "draft":
            raise GigSimError(f"proposal '{proposal_id}' is already {p['status']}")
        if self.s["connects"] < p["connects_required"]:
            raise GigSimError(f"insufficient Connects: need {p['connects_required']}, have {self.s['connects']}")
        self.s["connects"] -= p["connects_required"]  # no await between check and spend -> atomic
        p.update(status="submitted", connects_spent=p["connects_required"], submitted_at=_now())
        self.s["keys"][f"submit:{idempotency_key}"] = proposal_id
        self._save()
        return p

    async def send_message(self, thread_id, body, idempotency_key):
        if (rid := self._replay("msg", idempotency_key)):
            return next(m for t in self.s["threads"].values() for m in t["messages"] if m["message_id"] == rid)
        if thread_id not in self.s["threads"]:
            raise GigSimError(f"thread '{thread_id}' not found")
        m = {"message_id": f"msg_{uuid.uuid4().hex[:8]}", "thread_id": thread_id, "sender": "freelancer", "body": body, "sent_at": _now()}
        self.s["threads"][thread_id]["messages"].append(m)
        self.s["keys"][f"msg:{idempotency_key}"] = m["message_id"]
        self._save()
        return m


ROUTES = {  # <-- verify against your GigSim OpenAPI spec
    "search_jobs": ("GET", "/jobs"), "get_job": ("GET", "/jobs/{id}"), "client": ("GET", "/clients/{id}"),
    "connects": ("GET", "/me/connects"), "messages": ("GET", "/messages"), "get_proposal": ("GET", "/proposals/{id}"),
    "create_proposal": ("POST", "/proposals"), "submit_proposal": ("POST", "/proposals/{id}/submit"),
    "send_message": ("POST", "/threads/{id}/messages"),
}


class HttpGigSim:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        self.http = httpx.AsyncClient(base_url=base_url, timeout=timeout, headers={"Authorization": f"Bearer {api_key}"})

    async def _req(self, route: str, *, id: str | None = None, params=None, json_body=None, key: str | None = None):
        method, path = ROUTES[route]
        headers = {"Idempotency-Key": key} if key else {}
        try:
            r = await self.http.request(method, path.format(id=id), params=params, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise ConnectionError(f"GigSim unreachable: {exc}") from exc
        if r.status_code == 404:
            raise GigSimError("not found")
        if r.status_code in (400, 402, 409, 422):
            raise GigSimError(r.json().get("detail", r.text)[:300] if r.headers.get("content-type", "").startswith("application/json") else r.text[:300])
        r.raise_for_status()
        return r.json()

    async def search_jobs(self, query, *, job_type=None, min_budget=None, limit=10):
        return await self._req("search_jobs", params={"q": query, "type": job_type, "min_budget": min_budget, "limit": limit})

    async def get_job(self, job_id): return await self._req("get_job", id=job_id)
    async def get_client_history(self, client_id): return await self._req("client", id=client_id)
    async def get_connects_balance(self): return await self._req("connects")
    async def list_messages(self, thread_id=None, job_id=None, limit=20):
        return await self._req("messages", params={"thread_id": thread_id, "job_id": job_id, "limit": limit})
    async def get_proposal(self, proposal_id): return await self._req("get_proposal", id=proposal_id)
    async def create_proposal_draft(self, payload, idempotency_key):
        return await self._req("create_proposal", json_body=payload, key=idempotency_key)
    async def submit_proposal(self, proposal_id, idempotency_key):
        return await self._req("submit_proposal", id=proposal_id, key=idempotency_key)
    async def send_message(self, thread_id, body, idempotency_key):
        return await self._req("send_message", id=thread_id, json_body={"body": body}, key=idempotency_key)


def backend_from_env() -> GigSimBackend:
    if os.getenv("GIGSIM_BASE_URL"):
        return HttpGigSim(os.environ["GIGSIM_BASE_URL"], os.environ["GIGSIM_API_KEY"])
    return LocalMockGigSim(os.getenv("GIGSIM_STATE_PATH"))
