"""Pydantic hand-offs. Anything a later step decides on is one of these objects, never free text."""
from typing import Literal

from pydantic import BaseModel, Field


class JobSpec(BaseModel):
    job_id: str
    title: str
    deliverables: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    budget_min: float | None = None
    budget_max: float | None = None
    timeline: str | None = None
    screening_questions: list[str] = Field(default_factory=list)


class RiskReport(BaseModel):
    score: float = Field(ge=0, le=1)
    flags: list[str] = Field(default_factory=list)
    explanation: str


class FitReport(BaseModel):
    score: float = Field(ge=0, le=1)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class BidDecision(BaseModel):
    action: Literal["skip", "watch", "bid"]
    reason: str
    risk_score: float = Field(ge=0, le=1)
    fit_score: float = Field(ge=0, le=1)


class PriceQuote(BaseModel):
    amount: float = Field(ge=0)
    currency: str = "USD"
    connects_cost: int = Field(ge=0, default=0)
    connects_balance: int | None = None
    rationale: str


class ProposalDraft(BaseModel):
    job_id: str
    cover_letter: str
    screening_answers: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    status: str = "pending_review"


class Critique(BaseModel):
    approved: bool
    score: float = Field(ge=0, le=1)
    issues: list[str] = Field(default_factory=list)
    send_back: bool = False          # True only when the graph will really loop back to the writer
    revision: int = 0                # how many times the draft has been sent back so far


class ComplianceVerdict(BaseModel):
    allowed: bool
    violations: list[str] = Field(default_factory=list)
    reason: str = ""
