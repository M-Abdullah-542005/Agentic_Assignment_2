"""Explicit rules for what may become long-term memory.

Rule 1 (raw output): tool output, traces and whole drafts are NEVER stored. Only small typed records built here.
Rule 2 (style): a word the user removes from a draft becomes a candidate. It is promoted to a rule only after
                it was removed in STYLE_MIN_SUPPORT different edits. Removing the whole first sentence is a
                candidate "cut_first_sentence" with the same support rule.
Rule 3 (outcome): only a final outcome (replied / hired / no_reply / rejected) is promoted, and only the
                whitelisted fields below. Counters per job category; evidence ids that were in winning
                proposals are kept so the writer can prefer them.
"""
import difflib
import re

STYLE_MIN_SUPPORT = 2
FINAL_OUTCOMES = {"replied", "hired", "no_reply", "rejected"}
OUTCOME_FIELDS = {"status", "price", "evidence_ids"}
RAW_KEYS = {"raw", "tool_output", "result", "trace", "response", "body"}
IGNORE_WORDS = {"the", "and", "for", "with", "that", "this", "you", "your", "have", "has", "are", "was", "but", "not",
                "can", "will", "from", "our", "who", "its", "his", "her"}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z'-]+", text.lower())


def first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return parts[0] if parts else ""


def edit_diff(original: str, edited: str) -> str:
    return "\n".join(difflib.unified_diff(original.splitlines(), edited.splitlines(), "draft", "edited", lineterm=""))


def removed_words(original: str, edited: str) -> list[str]:
    """Words that were in the draft and are gone after the edit (word level diff)."""
    a, b = words(original), words(edited)
    gone = []
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag in ("delete", "replace"):
            gone += a[i1:i2]
    return [w for w in dict.fromkeys(gone) if len(w) >= 4 and w not in IGNORE_WORDS and w not in b]


def style_candidates(original: str, edited: str) -> list[dict]:
    """Turn one edit into candidate rules (not yet promoted)."""
    cands = [{"type": "avoid_word", "value": w} for w in removed_words(original, edited)]
    fs = first_sentence(original)
    if fs and fs not in edited and len(original.split()) > len(edited.split()):
        cands.append({"type": "cut_first_sentence", "value": "1"})
    return cands


def should_promote_style(count: int) -> bool:
    return count >= STYLE_MIN_SUPPORT


def check_not_raw(record: dict) -> None:
    """Guard used before every memory write."""
    if set(record) & RAW_KEYS:
        raise ValueError(f"raw tool output cannot be written to memory: {sorted(set(record) & RAW_KEYS)}")
    for v in record.values():
        if isinstance(v, str) and len(v) > 300:
            raise ValueError("long free text cannot be written to memory")


def clean_outcome(outcome: dict) -> dict | None:
    """Keep only whitelisted fields of a FINAL outcome; return None if it is not promotable."""
    if outcome.get("status") not in FINAL_OUTCOMES:
        return None
    rec = {k: v for k, v in outcome.items() if k in OUTCOME_FIELDS}
    rec["evidence_ids"] = [str(e) for e in rec.get("evidence_ids", [])][:10]
    check_not_raw(rec)
    return rec


def apply_style_rules(text: str, rules: list[dict]) -> str:
    """Apply promoted style rules to a draft."""
    for r in rules:
        if r["type"] == "avoid_word":
            text = re.sub(r"\b%s\b\s*" % re.escape(r["value"]), "", text, flags=re.I)
        elif r["type"] == "cut_first_sentence":
            parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
            text = parts[1] if len(parts) > 1 else text
    return re.sub(r"[ \t]{2,}", " ", text).strip()
