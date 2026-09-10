
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Table, Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ============================================================
# CANDIDATE <-> SKILL (many to many)
# ============================================================
# An association TABLE rather than a plain link, because the relationship itself
# carries data: which words the candidate actually used, and whether the skill
# was declared in a skills section or found inside a job bullet.
#
# That second column is not decoration. A skill listed in a skills section is a
# claim; a skill demonstrated in a bullet ("built 12 services with Docker") is
# evidence. A recruiter weighs those differently, and later the ranker can too.

candidate_skills = Table(
    "candidate_skills",
    Base.metadata,
    Column("candidate_id", ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True),
    Column("skill_id", ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True),
    Column("surface_form", String(120)),
    Column("source", String(16), default="declared"),   # declared | inferred
)


class Skill(Base):
    """One canonical skill. Deduplicated across every candidate.

    Storing skills once and referencing them is what makes
    "how many shortlisted candidates have Kubernetes" a single indexed lookup
    instead of a scan over every candidate's text.
    """

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    # False when the vocabulary had never seen it. Kept, not dropped -- see
    # vocabulary.py -- but flagged so unknown skills can be reviewed.
    known: Mapped[bool] = mapped_column(Boolean, default=True)

    candidates: Mapped[list["Candidate"]] = relationship(
        secondary=candidate_skills, back_populates="skills"
    )

    def __repr__(self) -> str:
        return f"<Skill {self.canonical}>"


# ============================================================
# CANDIDATE
# ============================================================

class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The resume file's stem. Unique, and the join key to the vector store --
    # every chunk in Chroma carries this same resume_id in its metadata, which
    # is what lets the ranker search inside one candidate's chunks.
    resume_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    full_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(200))
    headline: Mapped[str | None] = mapped_column(String(200))

    # Derived, stored, indexed. See the module docstring for why.
    total_experience_months: Mapped[int] = mapped_column(Integer, default=0, index=True)
    highest_degree: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    currently_employed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Provenance. Which model produced this record, and when. With a mixed
    # provider pool this is the only way to trace an odd row to its source.
    source_file: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(80))
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    n_chunks_used: Mapped[int] = mapped_column(Integer, default=0)

    # Soft quality flags, e.g. "experience found but no usable dates". NOT a
    # reason to exclude anyone -- a reason to tell the recruiter that a filter
    # could not be applied to this candidate.
    issues: Mapped[list] = mapped_column(JSON, default=list)

    # Free text with no column of its own. Certifications are rarely filtered on
    # and vary wildly in wording, so they live as JSON rather than earning a
    # table. Semantic search still finds them via the chunks.
    certifications: Mapped[list] = mapped_column(JSON, default=list)

    skills: Mapped[list[Skill]] = relationship(
        secondary=candidate_skills, back_populates="candidates", lazy="selectin"
    )
    experience: Mapped[list["Experience"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="selectin"
    )
    education: Mapped[list["Education"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Candidate {self.resume_id} {self.full_name}>"

    @property
    def total_experience_years(self) -> float:
        return round(self.total_experience_months / 12, 1)


# The two filters that run on every ranking pass, so they get a composite index.
# One index on the pair is faster than two separate ones when both appear in the
# same WHERE clause.
Index("ix_candidate_filters", Candidate.total_experience_months, Candidate.highest_degree)


# ============================================================
# EXPERIENCE
# ============================================================

class Experience(Base):
    """One job. Its own table rather than JSON on the candidate, because you
    will want to query it: most recent employer, roles held at a given company,
    tenure distribution. JSON can store that; only rows can be queried."""

    __tablename__ = "experience"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )

    company: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))

    start_year: Mapped[int | None] = mapped_column(Integer)
    start_month: Mapped[int | None] = mapped_column(Integer)
    end_year: Mapped[int | None] = mapped_column(Integer)
    end_month: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_months: Mapped[int] = mapped_column(Integer, default=0)

    # position 0 is the most recent role, so "current title" is a cheap lookup
    # rather than a sort over dates that may be missing.
    position: Mapped[int] = mapped_column(Integer, default=0)

    # The bullets, kept verbatim. JSON because they are displayed as a block and
    # never filtered on individually -- the semantic half handles searching them.
    responsibilities: Mapped[list] = mapped_column(JSON, default=list)

    candidate: Mapped[Candidate] = relationship(back_populates="experience")

    def __repr__(self) -> str:
        return f"<Experience {self.title} @ {self.company}>"


# ============================================================
# EDUCATION
# ============================================================

class Education(Base):
    __tablename__ = "education"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )

    degree: Mapped[str | None] = mapped_column(String(200))
    # Stored as the enum's string value, not the Enum type, so that adding a
    # level later does not require a migration of existing rows.
    level: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    field_of_study: Mapped[str | None] = mapped_column(String(200))
    institution: Mapped[str | None] = mapped_column(String(200))
    start_year: Mapped[int | None] = mapped_column(Integer)
    end_year: Mapped[int | None] = mapped_column(Integer)

    candidate: Mapped[Candidate] = relationship(back_populates="education")

    def __repr__(self) -> str:
        return f"<Education {self.degree} @ {self.institution}>"
