"""Client Liaison (conversation graph): classify intent, draft a reply, offer interview slots."""
from agent.deps import Deps, call_tool
from agent.llm import ask
from agent.nodes.common import sentences
from agent.nodes.compliance import check_text

SCAM = ("telegram", "whatsapp", "gift card", "send money", "pay upfront", "upfront fee", "crypto", "outside upwork",
        "outside of upwork", "wire transfer", "western union", "ignore previous instructions")
INTERVIEW = ("interview", "call", "meet", "schedule", "available", "zoom", "chat live")
NEGOTIATION = ("budget", "price", "rate", "discount", "cheaper", "lower", "quote", "cost")


INSTRUCTIONS = (
    "Write a short, polite reply (under 90 words) from the freelancer to the client message. Use only facts from "
    "'evidence', 'quote' and 'slots'. If intent is interview_request, offer only the given slots. No links, no "
    "email or phone numbers, no guarantees, never move the chat off the platform. Reply with the message text only."
)


def classify_intent(text: str) -> str:
    low = text.lower()
    for name, words in (("scam_signal", SCAM), ("interview_request", INTERVIEW), ("negotiation", NEGOTIATION)):
        if any(w in low for w in words):
            return name
    return "question"


def classify_node(state, deps: Deps):
    msg = state["new_message"]
    intent = classify_intent(msg.get("text", ""))
    out = {"intent": intent, "messages": [*state.get("messages", []), msg]}
    if intent == "scam_signal":
        out["status"] = "scam_signal"
    return out


def draft_node(state, deps: Deps):
    trace, intent, text = [], state["intent"], state["new_message"].get("text", "")
    slots: list[str] = []
    ev: list[dict] = []
    if intent == "interview_request":
        if deps.workspace is not None:
            slots = call_tool(trace, "free_slots", deps.workspace.free_slots, n=3)
        reply = ("Thanks for reaching out, I would be glad to talk. Here are times that work for me: "
                 + "; ".join(slots) + ". Let me know which suits you." if slots else
                 "Thanks for reaching out, I would be glad to talk. What times work for you this week?")
    elif intent == "negotiation":
        q = state.get("quote")
        reply = (f"Thanks for being open about the budget. My quote of {q['amount']} {q.get('currency', 'USD')} reflects the scope in your post. "
                 if q else "Thanks for being open about the budget. ")
        reply += "I am happy to adjust the scope so the work fits your budget. Which parts matter most to you?"
    else:
        reply = "Thanks for the question."
        try:
            ev = deps.do_retrieve({"title": text, "description": text, "skills": [],
                                   "persona_id": getattr(deps, "persona_id", None)}, 2)
        except Exception:
            ev = []
        if ev and sentences(ev[0].get("text", "")):
            reply += f" On a related project, {sentences(ev[0]['text'])[0].rstrip('.')}."
        reply += " Let me know if you would like more detail."
    llm = ask(deps, "client_liaison", {"instructions": INSTRUCTIONS, "intent": intent, "message": text, "slots": slots,
                                      "quote": state.get("quote"), "evidence": ev, "history": state.get("messages", [])})
    if llm:
        reply = llm
    return {"draft_reply": reply, "slots": slots, "trace": trace}


def reply_compliance_node(state, deps: Deps):
    v = check_text(state["draft_reply"])
    out = {"compliance_verdict": v.model_dump()}
    if not v.allowed and not state.get("human_edited"):
        out["status"] = "blocked"
    return out
