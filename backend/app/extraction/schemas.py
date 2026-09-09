"""
Pydantic schemas for structured extraction from resumes.

WHAT THIS FILE IS FOR
---------------------
An LLM returns text, and text can say "about 5 years", "5+", "five", or
"2019-present". Your ranking code needs to evaluate `years >= 5`. This file is
the wall between the two: it declares the exact shape a candidate must have,
and anything that does not fit is rejected loudly instead of quietly poisoning
the database.

THE SCHEMA IS ALSO THE PROMPT
-----------------------------
`instructor` turns these models into a JSON schema and sends it to the model, so
every field name and every Field(description=...) below is literally part of the
instructions the LLM reads. That is why the descriptions are written as
instructions to a reader ("exactly as written on the resume", "null if not
stated") rather than as notes to a developer. Vague field names produce vague
extractions.

FOUR DESIGN DECISIONS WORTH UNDERSTANDING
-----------------------------------------
1. EXTRACT ONLY WHAT THE RANKER NEEDS.
   A resume contains dozens of facts. Every extra field is another thing the LLM
   can get wrong, another column to maintain, and more tokens per call. Each
   field here exists because a hard filter, a score, or the evidence panel uses
   it. "Might be interesting later" is not a reason to extract something.

2. NEVER ASK THE LLM FOR ARITHMETIC.
   total_years_experience is COMPUTED from the date ranges, not asked for.
   Language models are unreliable at counting and will happily add overlapping
   jobs together. Ask the model for facts it can read off the page; compute
   everything derivable from those facts in Python, where it is deterministic
   and testable.

3. EVERYTHING UNCERTAIN IS OPTIONAL.
   Half of real resumes omit a graduation year. If the field is required, the
   LLM will invent one rather than fail the schema -- you have turned a missing
   value into a confident lie. Optional fields with "null if not stated" in the
   description give the model a legal way to say "I don't know", which is what
   you actually want.

4. NO PROTECTED ATTRIBUTES, DELIBERATELY.
   There is no gender, age, nationality, marital status or photo field here, and
   there never should be. The ranker cannot use what it never extracts. This is
   the first and cheapest fairness control in the system, and it is worth being
   explicit that its absence is a decision, not an oversight.

   Note this does NOT make the system bias-free: names, universities and career
   gaps are all proxies that survive extraction, which is exactly why the bias
   audit at step 19 exists.

PRODUCTION vs LEARNING SHORTCUT
------------------------------
Production systems usually version their schema and store the raw LLM response
alongside the parsed one, so a schema change can be replayed over old resumes
without re-running the model. We store the model name and timestamp here, which
is the cheap half of that idea.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

CURRENT_YEAR = date.today().year
EARLIEST_PLAUSIBLE_YEAR = 1950


# ============================================================
# ENUMS
# ============================================================
# An Enum constrains the LLM to a fixed vocabulary. Without it you get
# "Bachelors", "B.Tech", "Undergraduate" and "bachelor's degree" as four
# different values for one concept, and every downstream filter has to handle
# all four. instructor puts the allowed values into the JSON schema, so the
# model sees the list and picks from it.

class DegreeLevel(str, Enum):
    SCHOOL = "school"          # class 10 / 12 / secondary
    DIPLOMA = "diploma"
    BACHELOR = "bachelor"      # B.Tech, B.E., BCA, B.Sc
    MASTER = "master"          # M.Tech, MCA, MBA, M.S.
    DOCTORATE = "doctorate"    # PhD
    UNKNOWN = "unknown"


# Ordering matters for "highest degree", and an Enum has no natural order,
# so the ranking is stated explicitly rather than left to alphabetical luck.
DEGREE_RANK = {
    DegreeLevel.UNKNOWN: 0,
    DegreeLevel.SCHOOL: 1,
    DegreeLevel.DIPLOMA: 2,
    DegreeLevel.BACHELOR: 3,
    DegreeLevel.MASTER: 4,
    DegreeLevel.DOCTORATE: 5,
}


# ============================================================
# SKILL
# ============================================================

class ExtractedSkill(BaseModel):
    """A skill as the candidate wrote it, plus a normalised form.

    Two fields rather than one because the resume says "Torch" and the job
    description says "PyTorch". You need BOTH: the surface form to show the
    recruiter what the candidate actually claimed, and the canonical form so a
    hard filter for "PyTorch" matches.

    `canonical` is deliberately NOT filled by the LLM. Normalisation is a lookup
    against a controlled vocabulary you own, which is deterministic, free, and
    correctable. Asking a language model to normalise means the same input can
    map differently on different days.
    """

    name: str = Field(
        description="The skill exactly as written on the resume, e.g. 'Torch', "
                    "'Power BI (DAX)', 'RESTful services'."
    )
    canonical: Optional[str] = Field(
        default=None,
        description="Leave null. This is filled in later by a lookup table, "
                    "not by you.",
    )

    @field_validator("name")
    @classmethod
    def clean(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip(" .,;•-")


# ============================================================
# EXPERIENCE
# ============================================================

class ExtractedExperience(BaseModel):
    """One job. Dates are split into year and month integers rather than kept as
    a string, because "Jan 2021 - Mar 2023", "01/2021 - 03/2023" and
    "2021 to 2023" all have to become the same comparable thing.

    Months are optional and years are not, because a resume that omits the month
    is normal and a resume that omits the year is unusable.
    """

    company: str = Field(description="Employer name, exactly as written.")
    title: str = Field(description="Job title, exactly as written.")

    start_year: Optional[int] = Field(
        default=None, description="Four-digit year the role started. Null if not stated."
    )
    start_month: Optional[int] = Field(
        default=None, description="Month the role started, 1-12. Null if only a year is given."
    )
    end_year: Optional[int] = Field(
        default=None, description="Four-digit year the role ended. Null if this is the current role."
    )
    end_month: Optional[int] = Field(
        default=None, description="Month the role ended, 1-12. Null if not stated or still current."
    )
    is_current: bool = Field(
        default=False,
        description="True if the resume says Present, Current, Ongoing or Till Date.",
    )

    responsibilities: list[str] = Field(
        default_factory=list,
        description="The bullet points under this role, one string each, "
                    "copied verbatim. Do not summarise or rewrite them.",
    )

    @field_validator("start_month", "end_month")
    @classmethod
    def month_in_range(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        # Out-of-range means the model misread something. Discarding the month
        # keeps the usable year rather than throwing the whole job away.
        return v if 1 <= v <= 12 else None

    @field_validator("start_year", "end_year")
    @classmethod
    def year_is_plausible(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        # Catches the classic failure of reading "2 years" or a phone fragment
        # as a year. Allows next year, since resumes list expected end dates.
        if EARLIEST_PLAUSIBLE_YEAR <= v <= CURRENT_YEAR + 1:
            return v
        return None

    @model_validator(mode="after")
    def dates_make_sense(self) -> "ExtractedExperience":
        if self.is_current:
            # "Present" and an end date cannot both be true. Trust the word.
            self.end_year, self.end_month = None, None
        if self.start_year and self.end_year and self.end_year < self.start_year:
            # Reversed range, usually a misread. Swap rather than discard.
            self.start_year, self.end_year = self.end_year, self.start_year
        return self

    @property
    def start_index(self) -> Optional[int]:
        """Absolute month index, so ranges can be compared and merged."""
        if self.start_year is None:
            return None
        return self.start_year * 12 + (self.start_month or 1) - 1

    @property
    def end_index(self) -> Optional[int]:
        if self.is_current:
            today = date.today()
            return today.year * 12 + today.month - 1
        if self.end_year is None:
            return None
        # Unknown months default to January at BOTH ends. Defaulting start to
        # January and end to December silently adds up to 11 months per job,
        # which is how "2021 - 2023" became 2.9 years instead of 2.
        return self.end_year * 12 + (self.end_month or 1) - 1

    @property
    def duration_months(self) -> int:
        if self.start_index is None or self.end_index is None:
            return 0
        return max(0, self.end_index - self.start_index)


# ============================================================
# EDUCATION
# ============================================================

class ExtractedEducation(BaseModel):
    degree: str = Field(
        description="The qualification as written, e.g. 'B.Tech in Computer Science', 'MCA'."
    )
    level: DegreeLevel = Field(
        description="Which level this qualification is. Use 'unknown' if unclear."
    )
    field_of_study: Optional[str] = Field(
        default=None, description="Subject only, e.g. 'Computer Science'. Null if not stated."
    )
    institution: Optional[str] = Field(
        default=None, description="School or university name. Null if not stated."
    )
    start_year: Optional[int] = Field(default=None, description="Null if not stated.")
    end_year: Optional[int] = Field(
        default=None, description="Graduation year. Null if not stated or ongoing."
    )

    @field_validator("start_year", "end_year")
    @classmethod
    def year_is_plausible(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        return v if EARLIEST_PLAUSIBLE_YEAR <= v <= CURRENT_YEAR + 6 else None


# ============================================================
# THE FULL PROFILE (what the LLM returns)
# ============================================================

class ResumeExtraction(BaseModel):
    """Everything we ask the LLM to read off one resume.

    Note what is NOT here: no gender, age, date of birth, nationality, marital
    status or photo. See the module docstring -- their absence is a fairness
    decision, not an oversight.
    """

    full_name: Optional[str] = Field(default=None, description="Candidate's full name.")
    email: Optional[str] = Field(default=None, description="Email address. Null if absent.")
    phone: Optional[str] = Field(default=None, description="Phone number. Null if absent.")
    location: Optional[str] = Field(
        default=None, description="City or region only, not a full street address."
    )
    headline: Optional[str] = Field(
        default=None,
        description="The candidate's current or most recent job title, one short line.",
    )

    skills: list[ExtractedSkill] = Field(
        default_factory=list,
        description="Every distinct skill, tool or technology mentioned anywhere, "
                    "including inside job bullets, not only the skills section.",
    )
    experience: list[ExtractedExperience] = Field(
        default_factory=list, description="Every role held, most recent first."
    )
    education: list[ExtractedEducation] = Field(
        default_factory=list, description="Every qualification, highest first."
    )
    certifications: list[str] = Field(
        default_factory=list, description="Certification names, one string each."
    )

    @field_validator("skills")
    @classmethod
    def dedupe_skills(cls, skills: list[ExtractedSkill]) -> list[ExtractedSkill]:
        # LLMs list the same skill twice when it appears in two sections. Keep
        # first occurrence, compare case-insensitively.
        seen, out = set(), []
        for s in skills:
            key = s.name.lower()
            if key and key not in seen:
                seen.add(key)
                out.append(s)
        return out

    @model_validator(mode="after")
    def sort_chronologically(self) -> "ResumeExtraction":
        self.experience.sort(key=lambda e: e.start_index or -1, reverse=True)
        self.education.sort(key=lambda e: e.end_year or -1, reverse=True)
        return self

    # ---------- computed, never asked of the LLM ----------

    @property
    def total_experience_months(self) -> int:
        """Total professional experience, with overlapping roles counted once.

        Summing each job's duration double-counts anyone who held two roles at
        the same time, or who lists a promotion as a separate entry overlapping
        the previous title. Merging the intervals first is the correct answer
        and takes six lines -- which is exactly why we do it here rather than
        asking a language model to do arithmetic.
        """
        spans = [
            (e.start_index, e.end_index)
            for e in self.experience
            if e.start_index is not None and e.end_index is not None
        ]
        if not spans:
            return 0

        spans.sort()
        merged = [list(spans[0])]
        for start, end in spans[1:]:
            if start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return sum(end - start for start, end in merged)

    @property
    def total_experience_years(self) -> float:
        return round(self.total_experience_months / 12, 1)

    @property
    def highest_degree(self) -> DegreeLevel:
        if not self.education:
            return DegreeLevel.UNKNOWN
        return max((e.level for e in self.education), key=lambda l: DEGREE_RANK[l])

    @property
    def current_role(self) -> Optional[ExtractedExperience]:
        for e in self.experience:
            if e.is_current:
                return e
        return self.experience[0] if self.experience else None

    @property
    def skill_names(self) -> list[str]:
        """Canonical where known, surface form otherwise -- what filters match on."""
        return [s.canonical or s.name for s in self.skills]

    def completeness_issues(self) -> list[str]:
        """Soft quality flags. These are NOT validation errors.

        A resume with no dates is still a real candidate and must stay in the
        ranking. But the recruiter should be told the years-of-experience filter
        could not be applied to them, rather than silently seeing them ranked
        last. Missing data and disqualifying data are different things.
        """
        issues = []
        if not self.full_name:
            issues.append("no name found")
        if not self.experience:
            issues.append("no work experience found")
        if not self.skills:
            issues.append("no skills found")
        if self.experience and self.total_experience_months == 0:
            issues.append("experience found but no usable dates")
        if not self.education:
            issues.append("no education found")
        return issues


# ============================================================
# PROVENANCE
# ============================================================

class ExtractionMetadata(BaseModel):
    """Filled in by our code, never by the LLM.

    Kept separate from ResumeExtraction so the LLM's schema stays clean -- if
    these fields were mixed in, instructor would send them to the model and the
    model would try to fill them.
    """

    resume_id: str
    source_file: str
    source_type: str
    model: str = Field(description="Which LLM produced this, e.g. 'claude-sonnet-5'.")
    extracted_at: datetime = Field(default_factory=datetime.now)
    n_chunks_used: int = 0
    issues: list[str] = Field(default_factory=list)


class CandidateRecord(BaseModel):
    """One candidate, ready to write to the database."""

    metadata: ExtractionMetadata
    profile: ResumeExtraction

    @classmethod
    def build(
        cls,
        profile: ResumeExtraction,
        resume_id: str,
        source_file: str,
        source_type: str,
        model: str,
        n_chunks_used: int = 0,
    ) -> "CandidateRecord":
        return cls(
            metadata=ExtractionMetadata(
                resume_id=resume_id,
                source_file=source_file,
                source_type=source_type,
                model=model,
                n_chunks_used=n_chunks_used,
                issues=profile.completeness_issues(),
            ),
            profile=profile,
        )