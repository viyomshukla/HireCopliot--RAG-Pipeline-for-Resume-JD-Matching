"""
Likely-duplicate detection: the same person, loaded twice.

THE RULE
--------
Two candidates are a likely duplicate when they are in the SAME batch and have
the same full name AND the same email, compared case-insensitively with
surrounding whitespace ignored.

Both fields, not either. Name alone flags every pair of "Priya Sharma"s, who are
very often different people. Email alone flags a shared recruiter or agency
address pasted onto several resumes. The pair together is a strong signal and
rarely wrong.

Same batch only. Ranking is per batch, so a duplicate only wastes a shortlist
slot inside one batch -- and matching across batches would tell one recruiter
that a person also applied through someone else's upload.

WHAT IT PRODUCES
----------------
`duplicate_of` on every candidate after the first: the resume_id of the
EARLIEST matching candidate (lowest id). Every copy points at the same root, so
a group of three is A <- B, A <- C, never a chain A <- B <- C that a reader has
to walk. The root itself stays NULL.

Nothing is merged or hidden. It is a flag for the recruiter.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Candidate


def _norm(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip().lower()
    return value or None


def find_duplicate_of(session: Session, candidate: Candidate) -> Optional[str]:
    """resume_id of the earliest earlier candidate this one duplicates, or None.

    The candidate must already be flushed, so it has an id to compare against.
    """
    name, email = _norm(candidate.full_name), _norm(candidate.email)
    if not name or not email or candidate.id is None:
        return None

    query = (
        select(Candidate.resume_id)
        .where(Candidate.id < candidate.id)
        .where(func.lower(func.trim(Candidate.full_name)) == name)
        .where(func.lower(func.trim(Candidate.email)) == email)
        .order_by(Candidate.id)
        .limit(1)
    )
    # `IS` rather than `=`: candidates loaded by the CLI have no batch, and
    # NULL = NULL is not true in SQL.
    if candidate.job_id is None:
        query = query.where(Candidate.job_id.is_(None))
    else:
        query = query.where(Candidate.job_id == candidate.job_id)

    return session.scalar(query)


def backfill_duplicates(session: Session) -> int:
    """Recomputes the flag for every candidate. Returns how many are flagged.

    Walks in id order so each candidate is compared only against the ones
    before it, which is the same answer load-time detection gives.
    """
    flagged = 0
    for candidate in session.scalars(select(Candidate).order_by(Candidate.id)):
        candidate.duplicate_of = find_duplicate_of(session, candidate)
        flagged += candidate.duplicate_of is not None
    return flagged
