"""


    python scripts/load_database.py            # load / refresh
    python scripts/load_database.py --reset    # drop and rebuild first
    python scripts/load_database.py --stats    # just show what is in there

WHY THIS IS UPSERT, NOT INSERT
------------------------------
You will run it repeatedly while tuning extraction. Plain inserts would either
crash on the unique resume_id or quietly create duplicate candidates -- and a
duplicated candidate appears twice in a shortlist, which is the kind of bug a
recruiter notices before you do. Loading by resume_id and replacing the child
rows makes the script safe to re-run any number of times.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from sqlalchemy import func, select  # noqa: E402

from app.db.models import Candidate, Education, Experience, Skill, candidate_skills  # noqa: E402
from app.db.session import get_session, init_db  # noqa: E402
from app.extraction.vocabulary import canonicalise, reset_unknown, unknown_skills  # noqa: E402
from app.extraction.schemas import DEGREE_RANK, DegreeLevel, ResumeExtraction  # noqa: E402

EXTRACTIONS_FILE = BASE / "data" / "extractions.json"


def get_or_create_skill(session, canonical: str, known: bool) -> Skill:
    """One row per canonical skill, shared by every candidate who has it.

    This is what deduplication buys: 50 candidates with Python produce one
    Skill row and 50 association rows, not 50 copies of the string.
    """
    skill = session.scalar(select(Skill).where(Skill.canonical == canonical))
    if skill is None:
        skill = Skill(canonical=canonical, known=known)
        session.add(skill)
        session.flush()   # assigns the id we need for the association row
    return skill


def load_record(session, record: dict) -> tuple[Candidate, int, int]:
    meta = record["metadata"]
    # Re-validating through pydantic rather than reading the dict directly means
    # the computed properties (merged experience months, highest degree) come
    # from the ONE function that owns that logic, in schemas.py. Reimplementing
    # them here is how two parts of a system start disagreeing about a number.
    profile = ResumeExtraction.model_validate(record["profile"])

    candidate = session.scalar(
        select(Candidate).where(Candidate.resume_id == meta["resume_id"])
    )
    if candidate is None:
        candidate = Candidate(resume_id=meta["resume_id"])
        session.add(candidate)

    candidate.full_name = profile.full_name
    candidate.email = profile.email
    candidate.phone = profile.phone
    candidate.location = profile.location
    candidate.headline = profile.headline
    candidate.certifications = profile.certifications
    candidate.total_experience_months = profile.total_experience_months
    candidate.highest_degree = profile.highest_degree.value
    candidate.currently_employed = any(e.is_current for e in profile.experience)
    candidate.source_file = meta.get("source_file")
    candidate.source_type = meta.get("source_type")
    candidate.model = meta.get("model")
    candidate.n_chunks_used = meta.get("n_chunks_used", 0)
    candidate.issues = meta.get("issues", [])

    # Child rows are replaced wholesale rather than diffed. Extraction output is
    # the source of truth and it is cheap to rebuild; a merge would be more code
    # for no benefit and a real risk of stale rows surviving a reload.
    candidate.experience.clear()
    candidate.education.clear()
    session.flush()

    for position, exp in enumerate(profile.experience):
        candidate.experience.append(Experience(
            company=exp.company, title=exp.title,
            start_year=exp.start_year, start_month=exp.start_month,
            end_year=exp.end_year, end_month=exp.end_month,
            is_current=exp.is_current, duration_months=exp.duration_months,
            position=position, responsibilities=exp.responsibilities,
        ))

    for edu in profile.education:
        candidate.education.append(Education(
            degree=edu.degree, level=edu.level.value,
            field_of_study=edu.field_of_study, institution=edu.institution,
            start_year=edu.start_year, end_year=edu.end_year,
        ))

    # --- skills, with normalisation ---
    session.execute(
        candidate_skills.delete().where(candidate_skills.c.candidate_id == candidate.id)
    )

    seen: set[str] = set()
    unknown_count = 0
    for extracted in profile.skills:
        canonical, known = canonicalise(extracted.name)
        if not known:
            unknown_count += 1
        # Two surface forms can normalise to the same canonical skill
        # ("Torch" and "PyTorch" on one resume). The association is a composite
        # primary key, so inserting both would violate it.
        if canonical.lower() in seen:
            continue
        seen.add(canonical.lower())

        skill = get_or_create_skill(session, canonical, known)
        session.execute(candidate_skills.insert().values(
            candidate_id=candidate.id,
            skill_id=skill.id,
            surface_form=extracted.name[:120],
            source="declared",
        ))

    return candidate, len(seen), unknown_count


def show_stats(session) -> None:
    n_candidates = session.scalar(select(func.count()).select_from(Candidate))
    n_skills = session.scalar(select(func.count()).select_from(Skill))
    n_exp = session.scalar(select(func.count()).select_from(Experience))
    n_edu = session.scalar(select(func.count()).select_from(Education))

    print(f"\ncandidates {n_candidates}   skills {n_skills}   "
          f"experience {n_exp}   education {n_edu}")

    print("\nhighest degree:")
    for level, count in session.execute(
        select(Candidate.highest_degree, func.count())
        .group_by(Candidate.highest_degree)
    ):
        print(f"   {level:14} {count}")

    print("\nexperience distribution:")
    for label, lo, hi in [("0-2y", 0, 24), ("2-5y", 24, 60),
                          ("5-10y", 60, 120), ("10y+", 120, 10_000)]:
        n = session.scalar(
            select(func.count()).select_from(Candidate)
            .where(Candidate.total_experience_months >= lo)
            .where(Candidate.total_experience_months < hi)
        )
        print(f"   {label:6} {n}")

    print("\nmost common skills:")
    rows = session.execute(
        select(Skill.canonical, func.count(candidate_skills.c.candidate_id))
        .join(candidate_skills, Skill.id == candidate_skills.c.skill_id)
        .group_by(Skill.canonical)
        .order_by(func.count(candidate_skills.c.candidate_id).desc())
        .limit(10)
    ).all()
    for name, count in rows:
        print(f"   {count:3}  {name}")


def demo_queries(session) -> None:
    """The queries a vector store cannot answer. This is the whole argument for
    having a database, made concrete."""
    print("\n" + "=" * 60)
    print("WHAT THE STRUCTURED HALF CAN DO")
    print("=" * 60)

    # 1. a numeric comparison
    n = session.scalar(
        select(func.count()).select_from(Candidate)
        .where(Candidate.total_experience_months >= 60)
    )
    print(f"\n5+ years experience                     : {n} candidates")

    # 2. AND across two skills -- the query a text column cannot express
    py = select(candidate_skills.c.candidate_id).join(
        Skill, Skill.id == candidate_skills.c.skill_id
    ).where(Skill.canonical == "Python")
    docker = select(candidate_skills.c.candidate_id).join(
        Skill, Skill.id == candidate_skills.c.skill_id
    ).where(Skill.canonical == "Docker")
    n = session.scalar(
        select(func.count()).select_from(Candidate)
        .where(Candidate.id.in_(py)).where(Candidate.id.in_(docker))
    )
    print(f"Python AND Docker                       : {n} candidates")

    # 3. ABSENCE -- no vector equivalent exists
    k8s = select(candidate_skills.c.candidate_id).join(
        Skill, Skill.id == candidate_skills.c.skill_id
    ).where(Skill.canonical == "Kubernetes")
    n = session.scalar(
        select(func.count()).select_from(Candidate).where(~Candidate.id.in_(k8s))
    )
    print(f"WITHOUT Kubernetes                      : {n} candidates")

    # 4. a full hard filter, the kind a JD produces
    rows = session.execute(
        select(Candidate.resume_id, Candidate.full_name,
               Candidate.total_experience_months, Candidate.highest_degree)
        .where(Candidate.total_experience_months >= 60)
        .where(Candidate.highest_degree.in_(["master", "doctorate"]))
        .where(Candidate.id.in_(py))
        .order_by(Candidate.total_experience_months.desc())
        .limit(5)
    ).all()
    print(f"\n5+ years AND postgraduate AND Python    : {len(rows)} shown")
    for rid, name, months, degree in rows:
        print(f"   {rid}  {(name or '?')[:24]:24} {months/12:4.1f}y  {degree}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true",
                        help="drop and recreate the tables first")
    parser.add_argument("--stats", action="store_true",
                        help="show stats without loading")
    args = parser.parse_args()

    init_db(drop=args.reset)

    if args.stats:
        with get_session() as session:
            show_stats(session)
            demo_queries(session)
        return

    if not EXTRACTIONS_FILE.exists():
        sys.exit(f"missing {EXTRACTIONS_FILE} - run scripts/run_extraction.py first")

    records = json.loads(EXTRACTIONS_FILE.read_text(encoding="utf-8"))
    reset_unknown()

    loaded = 0
    failures: list[tuple[str, str]] = []
    flagged: Counter = Counter()

    with get_session() as session:
        for record in records:
            resume_id = record.get("metadata", {}).get("resume_id", "?")
            try:
                candidate, n_skills, n_unknown = load_record(session, record)
            except Exception as exc:
                failures.append((resume_id, f"{type(exc).__name__}: {exc}"))
                continue
            loaded += 1
            for issue in candidate.issues:
                flagged[issue] += 1

        session.flush()
        print(f"loaded {loaded}/{len(records)} candidates")
        for name, err in failures:
            print(f"  FAILED {name}: {err}")

        unknown = unknown_skills()
        if unknown:
            print(f"\nskills the vocabulary does not know "
                  f"({len(unknown)} shown, add the useful ones to vocabulary.py):")
            for name, count in unknown:
                print(f"   {count:3}  {name}")

        if flagged:
            print("\ncompleteness flags:")
            for issue, count in flagged.most_common():
                print(f"   {count:3}  {issue}")

        show_stats(session)
        demo_queries(session)


if __name__ == "__main__":
    main()