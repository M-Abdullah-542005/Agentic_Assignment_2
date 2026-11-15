from typing import Literal
from pydantic import BaseModel, Field
from agent.handoffs import BidDecision, ComplianceVerdict, Critique, FitReport, PriceQuote, ProposalDraft, RiskReport, JobSpec  # noqa

Persona = Literal["ai_ml_engineer", "fullstack_dev", "data_analyst"]

class LoginIn(BaseModel):
    """Log in, or create the account on first use. persona is required to create an account; hourly_rate and
    weekly_connects_budget only change the saved profile when they are sent."""
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(default="pass12345", min_length=8, max_length=200)
    persona: Persona | None = None
    hourly_rate: float | None = Field(default=None, gt=0, le=1000)
    weekly_connects_budget: int | None = Field(default=None, ge=0, le=500)
class SessionOut(BaseModel):
    token: str; user_id: str; persona: str
class MarketplaceTokenIn(BaseModel):
    token: str = Field(min_length=8, max_length=4096)       # sent in the body, never in the URL
class JobRow(BaseModel):
    job_id: str; title: str; fit: float; risk: float; action: str; price: float | None; connects: int; status: str
    outcome_stage: str = "drafted"
class ApprovalIn(BaseModel):
    action: Literal["approve", "edit", "reject"]
    cover_letter: str | None = None                      # bid edits: the edited letter (the key the graph reads)
    screening_answers: list[str] | None = None
    text: str | None = None                              # message-reply edits (and older clients for bids)
    reason: str | None = Field(default=None, max_length=500)
class RegenIn(BaseModel):
    paragraph: int = Field(ge=0)
class ReplyApprovalIn(ApprovalIn): pass
class IncomingIn(BaseModel):
    job_id: str; text: str = Field(min_length=1, max_length=5000); message_id: str
