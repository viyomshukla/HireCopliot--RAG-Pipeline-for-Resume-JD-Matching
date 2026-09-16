"""
Scoring and ranking: parsed JD + candidates -> a shortlist with evidence.

THE PIPELINE
------------
    1. SQL hard filter        100 candidates -> those who qualify
    2. per soft requirement   retrieve that candidate's best chunks, rerank
    3. weighted average       one score per candidate
    4. sort, cut              the shortlist HR asked for

WHY EVERY CANDIDATE IS SCORED INDIVIDUALLY
-------------------------------------------
Not one search over everyone. One search PER CANDIDATE, per requirement. A
global top-k returns chunks from maybe 18 of 100 people, and the other 82 get no
score at all -- you cannot rank someone the retriever never looked at, and the
recruiter never learns they were skipped.

CHECKABLE SOFT REQUIREMENTS USE SQL, NOT RETRIEVAL
---------------------------------------------------
A soft requirement naming a skill ("Exposure to Docker") has a definitive answer
in the database: the candidate either has Docker in their skills table or does
not. Scoring that by text similarity is strictly worse -- a chunk merely
mentioning containers would score highly, and a candidate who listed Docker in a
skills section with no prose about it would score low.

So: structured evidence when it exists, retrieval only when it does not.
Retrieval still runs for the evidence quote, because "has Docker" is a fact and
the recruiter wants the sentence.

FAILING A SOFT REQUIREMENT IS NOT ZERO
---------------------------------------
A candidate missing one nice-to-have out of six should lose a little, not be
destroyed. Scores are a weighted AVERAGE over requirements, so each one costs
its share and no more. Using a product, or zeroing the total on any miss, makes
one absent nice-to-have as fatal as a missing must-have -- which is exactly the
distinction hard requirements exist to draw.

WHAT THE RECRUITER SEES
-----------------------
Every score decomposes into per-requirement lines with a quote attached. That is
the difference between "0.71" and "met 5 of 7 requirements, here is the evidence
for each" -- and for an employment tool the second is the only defensible form.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.ranking.jd_parser import ParsedJD, JobRequirement

BASE = Path(__file__).resolve().parents[2]
DB_FILE = BASE / "data" / "candidates.db"

DEGREE_ORDER = {"unknown": 0, "school": 1, "diploma": 2,
                "bachelor": 3, "master": 4, "doctorate": 5}

# A skill confirmed in the structured database scores higher than any text
# match, because it is a fact rather than an inference. Not 1.0: extraction is
# ~92% accurate, so certainty here would be overclaiming.
STRUCTURED_MATCH_SCORE = 0.95


@dataclass
class RequirementResult:
    requirement: JobRequirement
    score: float                      # 0-1
    source: str                       # "database" | "retrieval" | "missing"
    evidence: Optional[str] = None
    evidence_chunk_id: Optional[str] = None

    @property
    def weighted(self) -> float:
        return self.score * self.requirement.weight


@dataclass
class CandidateScore:
    resume_id: str
    name: str
    years: float
    highest_degree: str
    passed_filter: bool
    filter_failures: list[str] = field(default_factory=list)
    results: list[RequirementResult] = field(default_factory=list)
    # The uploaded filename. Names are not unique; the file is what a recruiter
    # with the folder open can actually find.
    source_file: Optional[str] = None
    # resume_id of an earlier candidate in the batch with the same name and
    # email. See app/db/duplicates.py.
    duplicate_of: Optional[str] = None

    @property
    def score(self) -> float:
        """Weighted average over soft requirements.

        Average, not sum, so JDs with different numbers of requirements produce
        comparable scores -- otherwise a JD with ten requirements would give
        everyone higher totals than one with three.
        """
        total_weight = sum(r.requirement.weight for r in self.results)
        if not total_weight:
            return 0.0
        return sum(r.weighted for r in self.results) / total_weight

    @property
    def met(self) -> int:
        return sum(1 for r in self.results if r.score >= 0.5)


class CandidateScorer:
    def __init__(self, retriever, db_path: Path = DB_FILE):
        # retriever is a RerankedRetriever. Typed loosely on purpose: the scorer
        # only needs search_within_candidate, so a different retrieval stack
        # drops in without touching this file.
        self.retriever = retriever
        self.db_path = db_path

    # ---------- database ----------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def load_candidates(self, job_id: Optional[str] = None) -> dict[str, dict]:
        conn = self._connect()
        sql = ("SELECT id, resume_id, full_name, source_file, duplicate_of, "
               "total_experience_months, highest_degree FROM candidates")
        params: list = []
        if job_id:
            # Without this the scorer ranks every candidate ever uploaded. One
            # recruiter sees another company's applicants, and the shortlist is
            # drawn from a pool the requester never submitted.
            sql += " WHERE job_id = ?"
            params.append(job_id)
        rows = conn.execute(sql, params).fetchall()

        out = {}
        for r in rows:
            skills = conn.execute(
                "SELECT LOWER(s.canonical) AS c FROM candidate_skills cs "
                "JOIN skills s ON s.id = cs.skill_id WHERE cs.candidate_id = ?",
                (r["id"],),
            ).fetchall()
            out[r["resume_id"]] = {
                "name": r["full_name"] or "?",
                "source_file": r["source_file"],
                "duplicate_of": r["duplicate_of"],
                "months": r["total_experience_months"] or 0,
                "degree": r["highest_degree"] or "unknown",
                "skills": {s["c"] for s in skills},
            }
        conn.close()
        return out

    # ---------- hard filter ----------

    @staticmethod
    def apply_hard_filter(candidate: dict, jd: ParsedJD) -> list[str]:
        """Returns the reasons this candidate fails. Empty list means they pass.

        Reasons are returned rather than a boolean because a rejected candidate
        must be explainable. "3 years, requirement was 5+" is something a company
        can defend; silent removal is not.
        """
        spec = jd.filter_spec()
        failures = []

        if spec["min_years"] and candidate["months"] / 12 < spec["min_years"]:
            failures.append(
                f"{candidate['months'] / 12:.1f} years, needs {spec['min_years']}+"
            )

        if spec["min_degree"]:
            have = DEGREE_ORDER.get(candidate["degree"], 0)
            need = DEGREE_ORDER[spec["min_degree"]]
            if have < need:
                failures.append(f"{candidate['degree']}, needs {spec['min_degree']}")

        for skill in spec["required_skills"]:
            if skill.lower() not in candidate["skills"]:
                failures.append(f"missing {skill}")
        for requirement in jd.hard_requirements:
            if requirement.skill_alternatives:
                alts = {s.lower() for s in requirement.skill_alternatives}
                if not (alts & candidate["skills"]):
                    failures.append(f"none of {', '.join(sorted(alts))}")
        return failures

    # ---------- scoring ----------

    def score_requirement(
        self, requirement: JobRequirement, resume_id: str, candidate: dict
    ) -> RequirementResult:
        # A named skill has a definitive answer in the database. Use it.
        if requirement.canonical_skill:
            has_it = requirement.canonical_skill.lower() in candidate["skills"]
            evidence = None
            chunk_id = None
            if has_it:
                # Still retrieve, purely for the quote to show the recruiter.
                hits = self.retriever.search_within_candidate(
                    requirement.canonical_skill, resume_id, n_results=1
                )
                if hits:
                    evidence = hits[0]["text"]
                    chunk_id = hits[0]["chunk_id"]
            return RequirementResult(
                requirement=requirement,
                score=STRUCTURED_MATCH_SCORE if has_it else 0.0,
                source="database" if has_it else "missing",
                evidence=evidence,
                evidence_chunk_id=chunk_id,
            )

        # Otherwise it is a judgement about text, which is what retrieval is for.
        hits = self.retriever.search_within_candidate(
            requirement.search_query, resume_id, n_results=1
        )
        if not hits:
            return RequirementResult(requirement=requirement, score=0.0, source="missing")

        best = hits[0]
        return RequirementResult(
            requirement=requirement,
            score=float(best.get("score", 0.0)),
            source="retrieval",
            # Only show the quote if the reranker considered it genuinely
            # relevant. A weak score still counts toward the ranking, but an
            # irrelevant quote presented as evidence is worse than none.
            evidence=best["text"] if best.get("above_floor", True) else None,
            evidence_chunk_id=best["chunk_id"],
        )

    def rank(self, jd, top_n=None, include_filtered_out=True,
             job_id: Optional[str] = None):
        candidates = self.load_candidates(job_id)
        soft = jd.soft_requirements

        scored: list[CandidateScore] = []
        for resume_id, candidate in candidates.items():
            failures = self.apply_hard_filter(candidate, jd)
            entry = CandidateScore(
                resume_id=resume_id,
                name=candidate["name"],
                years=round(candidate["months"] / 12, 1),
                highest_degree=candidate["degree"],
                passed_filter=not failures,
                filter_failures=failures,
                source_file=candidate["source_file"],
                duplicate_of=candidate["duplicate_of"],
            )

            # Only qualifying candidates are scored. Retrieval plus reranking is
            # the expensive part, so the filter exists as much to save compute as
            # to enforce requirements.
            if not failures:
                entry.results = [
                    self.score_requirement(r, resume_id, candidate) for r in soft
                ]
            scored.append(entry)

        passed = [c for c in scored if c.passed_filter]
        failed = [c for c in scored if not c.passed_filter]
        passed.sort(key=lambda c: c.score, reverse=True)

        ranked = passed + (failed if include_filtered_out else [])
        return ranked[:top_n] if top_n else ranked


def format_shortlist(ranked: list[CandidateScore], jd: ParsedJD, top_n: int = 10) -> str:
    lines = [f"{jd.title or '(untitled)'}   filter: {jd.filter_spec()}", ""]
    qualified = [c for c in ranked if c.passed_filter]
    lines.append(f"{len(qualified)} of {len(ranked)} candidates passed the hard filter")
    lines.append("")

    for i, c in enumerate(qualified[:top_n], start=1):
        lines.append(f"{i:2}. {c.score:.3f}  {c.name[:26]:26} "
                     f"{c.years:4.1f}y {c.highest_degree:10} "
                     f"met {c.met}/{len(c.results)}")
        for r in sorted(c.results, key=lambda r: -r.weighted)[:3]:
            mark = {"database": "db", "retrieval": "..", "missing": "no"}[r.source]
            lines.append(f"       [{mark}] {r.score:.2f} w{r.requirement.weight}  "
                         f"{r.requirement.text[:52]}")
            if r.evidence:
                lines.append(f"              \"{r.evidence.split(chr(10))[0][:64]}\"")
        lines.append("")

    rejected = [c for c in ranked if not c.passed_filter]
    if rejected:
        lines.append(f"excluded by hard requirements ({len(rejected)}):")
        for c in rejected[:5]:
            lines.append(f"   {c.name[:26]:26} {'; '.join(c.filter_failures[:2])}")
    return "\n".join(lines)