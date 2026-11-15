"""Everything the nodes need from the outside world, in one place (easy to fake in tests).

marketplace: a SyncMarketplace (agent/marketplace_port.py). Same method names as the MarketplacePort:
  search_jobs(query, limit), get_job(job_id), get_client_history(client_id), get_connects_balance(),
  create_proposal_draft(payload, idempotency_key, dry_run), submit_proposal(proposal_id, idempotency_key, dry_run),
  send_message(thread_id, body, idempotency_key, dry_run).
workspace (optional): free_slots(n) -> list[str].
llm: agent.llm.LLMClient (or anything with .complete(node, payload) -> str | None). None = rule-based fallbacks.

Thresholds, budgets and user defaults are NOT decided here: Deps.from_config() reads them from agent/config.yaml.
The numbers below are only the last-resort values for tests that build Deps() by hand.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent.memory.store import Memory

PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text()


@dataclass
class Deps:
    marketplace: Any = None
    workspace: Any = None
    retrieve: Callable | None = None       # (job_dict, k) -> list[EvidenceChunk | dict]
    verify: Callable | None = None         # (draft, evidence) -> VerificationReport
    llm: Any = None                        # agent.llm.LLMClient
    fallback_llm: Any = None               # cheaper model, used when the main one fails
    memory: Memory = field(default_factory=Memory)
    user_id: str = "default"
    persona_id: str | None = None          # which portfolio the retriever searches (per user)
    hourly_rate: float = 40.0
    connects_budget: int | None = None     # weekly Connects budget of this user
    draft_only: bool = False               # live Upwork mode: only drafts, the user confirms on Upwork
    autonomy: dict = field(default_factory=lambda: {"auto_skip_low_fit": True})
    scout: dict = field(default_factory=dict)   # the `scout:` block of config.yaml (queries, limits)
    max_steps: int = 40
    max_tokens: int = 60000
    retries: int = 2
    backoff: float = 0.0
    timeout: float = 120.0
    risk_skip: float = 0.7
    fit_bid: float = 0.45
    fit_low: float = 0.25

    @classmethod
    def from_config(cls, cfg: dict, **overrides) -> "Deps":
        """Budgets, thresholds, autonomy and user defaults all come from config.yaml."""
        b, t, ud = cfg.get("budgets") or {}, cfg.get("thresholds") or {}, cfg.get("user_defaults") or {}
        base = cls()
        values = dict(
            max_steps=b.get("max_steps", base.max_steps), max_tokens=b.get("max_tokens", base.max_tokens),
            timeout=b.get("node_timeout_seconds", base.timeout), retries=b.get("node_retries", base.retries),
            backoff=b.get("backoff_seconds", 0.5),
            risk_skip=t.get("risk_skip", base.risk_skip), fit_bid=t.get("fit_bid", base.fit_bid),
            fit_low=t.get("fit_low", base.fit_low),
            autonomy=dict(cfg.get("autonomy") or base.autonomy),
            scout=dict(cfg.get("scout") or {}),
            hourly_rate=ud.get("hourly_rate", base.hourly_rate), connects_budget=ud.get("connects_budget"),
        )
        values.update(overrides)
        return cls(**values)

    def call_llm(self, node: str, payload: dict) -> str | None:
        """Compatibility method for tests: calls ask(self, node, payload)."""
        prompt = load_prompt(node)
        body = {**payload, "node": node}
        for fn in (self.llm, self.fallback_llm):
            if fn is None:
                continue
            if hasattr(fn, "complete"):
                out = fn.complete(node, payload)
                if out:
                    return out
            elif callable(fn):
                try:
                    out = fn(prompt, body)
                    if out:
                        return out
                except Exception:
                    time.sleep(self.backoff)
        return None

    def require_marketplace(self):
        """Nodes call this instead of touching self.marketplace: no port means a clear error, never a fake success."""
        if self.marketplace is None:
            raise RuntimeError("no marketplace connection for this user (connect a marketplace token in Settings)")
        return self.marketplace

    def do_retrieve(self, job: dict, k: int = 5) -> list[dict]:
        fn = self.retrieve
        if fn is None:
            from rag.retriever import retrieve as fn
        return [e if isinstance(e, dict) else e.to_dict() for e in fn(job, k)]

    def do_verify(self, draft: str, evidence: list[dict]):
        fn = self.verify
        if fn is None:
            from rag.verify import verify_proposal as fn
        return fn(draft, evidence)


def call_tool(trace: list, name: str, fn: Callable, **args) -> Any:
    """Run a tool and append one execution-trace entry (audit only, never memory)."""
    t0 = time.time()
    res = None
    try:
        res = fn(**args)
        obs = "ok"
        return res
    except Exception as e:
        obs = f"error: {e}"
        raise
    finally:
        trace.append({"tool": name, "arguments": json.dumps(args, default=str)[:500],
                      "result": str(res if res is not None else "")[:300], "observation": obs,
                      "latency_s": round(time.time() - t0, 4)})
