"""Long-term memory on top of a LangGraph store (InMemoryStore here, PostgresStore in production).

Namespaces:
  ("style_candidates", user)  -> {"type", "value", "count"}      (not yet trusted)
  ("style", user)             -> promoted rules                    (used by the writer)
  ("outcome", user, category) -> counters + winning evidence ids
  ("seen_jobs", user)         -> job fingerprints for the Scout
All writes go through promotion_rules first.
"""
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from agent.memory import promotion_rules as rules


class Memory:
    def __init__(self, store: BaseStore | None = None):
        self.store = store or InMemoryStore()

    # ---------------- style memory
    def learn_from_edit(self, user: str, original: str, edited: str) -> list[dict]:
        """Record one user edit. Returns the rules promoted by this edit (often empty)."""
        promoted = []
        for c in rules.style_candidates(original, edited):
            key = f"{c['type']}:{c['value']}"
            item = self.store.get(("style_candidates", user), key)
            count = (item.value["count"] if item else 0) + 1
            rec = {"type": c["type"], "value": c["value"], "count": count}
            rules.check_not_raw(rec)
            self.store.put(("style_candidates", user), key, rec)
            if rules.should_promote_style(count) and not self.store.get(("style", user), key):
                self.store.put(("style", user), key, {"type": c["type"], "value": c["value"]})
                promoted.append({"type": c["type"], "value": c["value"]})
        return promoted

    def style_rules(self, user: str) -> list[dict]:
        return [i.value for i in self.store.search(("style", user), limit=100)]

    # ---------------- outcome memory
    def record_outcome(self, user: str, category: str, outcome: dict) -> bool:
        """Promote a final outcome for a job category. Returns False if the outcome is not promotable."""
        rec = rules.clean_outcome(outcome)
        if rec is None:
            return False
        ns, key = ("outcome", user, category), "summary"
        item = self.store.get(ns, key)
        s = item.value if item else {"n": 0, "replies": 0, "hires": 0, "winning_evidence": {}}
        s["n"] += 1
        won = rec["status"] in ("replied", "hired")
        s["replies"] += int(won)
        s["hires"] += int(rec["status"] == "hired")
        if won:
            for e in rec["evidence_ids"]:
                s["winning_evidence"][e] = s["winning_evidence"].get(e, 0) + 1
        self.store.put(ns, key, s)
        return True

    def outcome_summary(self, user: str, category: str) -> dict:
        item = self.store.get(("outcome", user, category), "summary")
        return item.value if item else {"n": 0, "replies": 0, "hires": 0, "winning_evidence": {}}

    # ---------------- scout memory
    def seen(self, user: str, fingerprint: str) -> bool:
        return self.store.get(("seen_jobs", user), fingerprint) is not None

    def mark_seen(self, user: str, fingerprint: str) -> None:
        self.store.put(("seen_jobs", user), fingerprint, {"seen": True})
