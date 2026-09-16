"""
API request and response models.

WHY THESE ARE SEPARATE FROM THE DOMAIN MODELS
----------------------------------------------
schemas.py in app/extraction describes what an LLM must produce. models.py in
app/db describes what a database row looks like. Neither is what a frontend
should receive, and coupling them means every internal refactor becomes a
breaking API change for the client.

There is also a leakage problem. A Candidate row carries email, phone, the model
that extracted it, and the raw chunk text. A ranking response does not need most
of that, and an API that returns "whatever the object happens to hold" will one
day return something it should not. Listing the fields explicitly here makes
what leaves the server a deliberate decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ============================================================
# UPLOAD / JOBS
# ============================================================

JobState = Literal["queued", "ingesting", "extracting", "indexing", "ready", "failed"]


class FileReport(BaseModel):
    name: str
    status: str
    resume_id: Optional[str] = None
    n_chunks: int = 0
    detail: str = ""


class JobStatus(BaseModel):
    job_id: str
    state: JobState
    # Progress is a message plus a fraction rather than a percentage integer,
    # because the stages take wildly different times: extraction is minutes,
    # indexing is seconds. A single number would jump from 20% to 90%.
    stage_message: str = ""
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    n_files: int = 0
    n_parsed: int = 0
    n_chunks: int = 0
    n_candidates: int = 0
    created_at: datetime
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    files: list[FileReport] = Field(default_factory=list)


class UploadResponse(BaseModel):
    job_id: str
    state: JobState
    message: str


# ============================================================
# RANKING
# ============================================================

class RankRequest(BaseModel):
    job_id: str = Field(description="The upload batch to rank within.")
    jd_text: str = Field(min_length=50, description="The job description, as text.")
    shortlist_size: int = Field(default=45, ge=1, le=500)
    include_excluded: bool = Field(
        default=True,
        description="Return candidates removed by hard requirements, with the "
                    "reason. Recommended: a filter that silently deletes people "
                    "is how a broken requirement goes unnoticed.",
    )


class EvidenceItem(BaseModel):
    requirement: str
    weight: int
    score: float
    # "database" means a structured fact (the skills table), "retrieval" means a
    # judgement about text. The UI should present them differently -- one is
    # proof, the other is supporting context.
    source: Literal["database", "retrieval", "missing"]
    quote: Optional[str] = None


class RankedCandidate(BaseModel):
    rank: Optional[int] = None
    resume_id: str
    name: str
    headline: Optional[str] = None
    years: float
    highest_degree: str
    score: float
    requirements_met: int
    requirements_total: int
    passed_filter: bool
    exclusion_reasons: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class ParsedRequirement(BaseModel):
    text: str
    kind: Literal["hard", "soft"]
    weight: int
    skill: Optional[str] = None
    min_years: Optional[int] = None
    degree_level: Optional[str] = None


class RankResponse(BaseModel):
    job_id: str
    title: Optional[str] = None
    # The parsed rubric is returned so the recruiter can SEE what the system
    # decided the JD was asking for. A ranking whose criteria are invisible
    # cannot be challenged, and for a hiring tool being challengeable is the
    # point.
    requirements: list[ParsedRequirement]
    filter_spec: dict
    total_candidates: int
    passed_filter: int
    shortlist: list[RankedCandidate]
    excluded: list[RankedCandidate] = Field(default_factory=list)
    seconds: float


# ============================================================
# FAIRNESS
# ============================================================

class GroupStat(BaseModel):
    value: str
    total: int
    selected: int
    selection_rate: float
    reliable: bool


class AttributeAuditResponse(BaseModel):
    attribute: str
    description: str
    impact_ratio: Optional[float] = None
    flagged: bool
    groups: list[GroupStat]


class AuditResponse(BaseModel):
    job_id: str
    shortlist_size: int
    total_candidates: int
    attributes: list[AttributeAuditResponse]
    note: str