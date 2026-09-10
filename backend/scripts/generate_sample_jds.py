"""
Synthetic job description generator, with relevance labels computed from the
candidate ground truth.

WHY THIS EXISTS
---------------
Ranking needs a job description. Evaluating ranking needs to know WHICH
CANDIDATES ARE ACTUALLY RIGHT for it. Without labels, "recall@45" cannot be
computed and every retrieval decision from here on is guesswork.

Hand-labelling 50 candidates against 8 JDs is 400 judgements, and they would be
inconsistent. Instead the labels are DERIVED: candidates.json already records
every candidate's canonical skills, total years and highest degree, so once a JD
states "5+ years, must have Python and Docker", who qualifies is arithmetic.

That is the same trick as the resume generator, one level up: build the artefact
FROM a spec, and the answer key falls out for free.

GRADED RELEVANCE, NOT BINARY
----------------------------
Real hiring is not relevant/irrelevant. A backend engineer with 6 years and
every required skill is a better match than one with 5 years and most of them,
and both beat a data analyst. So each candidate gets a grade 0-3 per JD.

Graded labels are what make nDCG possible at step 13, and nDCG is the metric
that actually reflects ranking quality -- it rewards putting the BEST candidate
first, not merely putting a relevant one somewhere in the top 45.

THE DESIGN DECISION THAT MAKES OR BREAKS THIS
----------------------------------------------
Soft requirements are deliberately phrased UNLIKE the resume text.

If a JD said "Built NLP pipelines for text classification" and a resume said the
same words, BM25 alone would score perfectly and hybrid retrieval would prove
nothing. So the JD says "experience applying language models to unstructured
text" instead. Now keyword search misses it and dense retrieval has to earn its
place -- which is exactly the claim the project makes.

Hard requirements are the opposite: they use CANONICAL skill names, because they
become SQL filters and those need exact matches against the skills table.

WHAT THE EVALUATION CAN AND CANNOT TELL YOU
--------------------------------------------
These labels measure whether retrieval finds the candidates who match on
STATED, CHECKABLE attributes. They cannot measure whether the ranking reflects
what a human recruiter would actually want -- judgement, trajectory, whether
someone is a good fit for the team. Synthetic labels test the machinery, not the
hiring. Worth stating plainly in the README rather than implying the numbers
mean more than they do.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
JD_DIR = BASE / "data" / "sample_jds"
GROUND_TRUTH_FILE = BASE / "data" / "ground_truth" / "jds.json"
CANDIDATES_FILE = BASE / "data" / "ground_truth" / "candidates.json"


# ============================================================
# ROLE SPECS
# ============================================================
# must_have / nice_to_have use CANONICAL skill names (they become SQL filters).
# soft_requirements are written to describe the same work in DIFFERENT words
# from anything in the resumes -- see the module docstring.

ROLE_SPECS = {
    "Backend Developer": {
        "titles": ["Senior Backend Engineer", "Backend Developer", "Platform Engineer"],
        "must_have": ["Python", "REST APIs", "PostgreSQL"],
        "nice_to_have": ["Docker", "Redis", "Kubernetes", "Message Queues"],
        "soft_requirements": [
            "has taken a service from design through to production and stayed on call for it",
            "comfortable reasoning about database performance under load",
            "has handled a production incident and written up what went wrong",
            "able to make an unfamiliar codebase safe to change",
        ],
        "min_years": [3, 5, 6],
    },
    "ML Engineer": {
        "titles": ["Machine Learning Engineer", "Applied ML Engineer", "ML Platform Engineer"],
        "must_have": ["Python", "Machine Learning", "PyTorch"],
        "nice_to_have": ["NLP", "MLOps", "Docker", "Deep Learning"],
        "soft_requirements": [
            "experience applying language models to unstructured text",
            "has shipped a model that real users depend on, not only notebooks",
            "understands why an offline metric can improve while the product gets worse",
            "comfortable diagnosing where a model fails rather than only measuring that it does",
        ],
        "min_years": [2, 4, 6],
    },
    "Data Analyst": {
        "titles": ["Senior Data Analyst", "Business Analyst", "Analytics Partner"],
        "must_have": ["SQL", "Power BI", "Statistics"],
        "nice_to_have": ["Python", "ETL", "A/B Testing", "Excel"],
        "soft_requirements": [
            "can turn a vague business question into something measurable",
            "has told a senior stakeholder something they did not want to hear",
            "comfortable working with data that is incomplete or disagrees with itself",
            "has automated away work that used to be done by hand",
        ],
        "min_years": [2, 3, 5],
    },
    "Data Scientist": {
        "titles": ["Data Scientist", "Senior Data Scientist", "Product Data Scientist"],
        "must_have": ["Python", "Machine Learning", "SQL"],
        "nice_to_have": ["Statistics", "A/B Testing", "Experimentation", "Scikit-learn"],
        "soft_requirements": [
            "has designed an experiment and defended its conclusion",
            "able to estimate impact when a clean controlled test is not possible",
            "communicates uncertainty honestly to non-technical audiences",
            "has replaced a hand-written rule with something learned from data",
        ],
        "min_years": [3, 5, 7],
    },
    "Software Engineer": {
        "titles": ["Software Engineer", "Senior Software Engineer", "Full Stack Engineer"],
        "must_have": ["Python", "SQL", "Git"],
        "nice_to_have": ["Docker", "REST APIs", "Unit Testing", "Java"],
        "soft_requirements": [
            "leaves code better than they found it and can say why",
            "has mentored someone more junior through a hard problem",
            "comfortable with ambiguity in requirements",
            "has argued for a simpler solution and won",
        ],
        "min_years": [2, 4, 6],
    },
}

COMPANIES = [
    "Northwind Systems", "Meridian Labs", "Cobalt Analytics", "Harbour Technologies",
    "Silverpine Digital", "Ironwood Software", "Bluecrest Data", "Quantum Ridge",
]

DEGREE_RANK = {"unknown": 0, "school": 1, "diploma": 2,
               "bachelor": 3, "master": 4, "doctorate": 5}
GT_DEGREE = {"undergraduate": "bachelor", "postgraduate": "master"}


# ============================================================
# JD TEXT
# ============================================================

def render_jd(spec: dict) -> str:
    """Renders a JD that reads like a real posting.

    Deliberately includes filler -- team blurb, benefits -- because a real JD
    does, and the JD parser at step 14 has to find the requirements inside the
    noise rather than in a tidy list.
    """
    lines = [
        f"{spec['title']}",
        f"{spec['company']} | {spec['location']}",
        "",
        "About the role",
        f"We are hiring a {spec['title']} to join a small team that owns "
        f"{spec['product']}. You will work closely with product and design, and "
        f"your work will be used by customers from the first month.",
        "",
        "What we are looking for",
        f"- {spec['min_years']}+ years of professional experience",
    ]
    if spec["required_degree"]:
        lines.append(
            f"- {spec['required_degree'].title()}'s degree in Computer Science "
            f"or a related field"
        )
    for skill in spec["must_have"]:
        lines.append(f"- Strong hands-on experience with {skill}")
    for requirement in spec["soft_requirements"]:
        # Bulleted as written rather than wrapped in "Someone who ...", because
        # the requirements are phrased as complete statements and forcing them
        # into a frame produced broken sentences.
        lines.append(f"- {requirement[0].upper()}{requirement[1:]}")

    lines += ["", "Nice to have"]
    for skill in spec["nice_to_have"]:
        lines.append(f"- Exposure to {skill}")

    lines += [
        "",
        "What we offer",
        "- Flexible hours and a hybrid working pattern",
        "- A budget for conferences and courses",
        "- Health cover for you and your family",
        "",
        "We review applications on a rolling basis and aim to reply within a week.",
    ]
    return "\n".join(lines)


# ============================================================
# RELEVANCE LABELS
# ============================================================

def grade_candidate(candidate: dict, spec: dict) -> tuple[int, dict]:
    """Grades one candidate against one JD. Returns (grade 0-3, why).

    The `why` dict is not decoration: when step 13 reports poor recall, the
    first question is always whether retrieval failed or the label is wrong,
    and that is only answerable if the label shows its working.
    """
    skills = {s.lower() for s in candidate["canonical_skills"]}
    must = {s.lower() for s in spec["must_have"]}
    nice = {s.lower() for s in spec["nice_to_have"]}

    must_hits = must & skills
    nice_hits = nice & skills
    years_ok = candidate["total_years_experience"] >= spec["min_years"]
    degree_ok = (
        spec["required_degree"] is None
        or DEGREE_RANK[GT_DEGREE.get(candidate["highest_degree_level"], "unknown")]
        >= DEGREE_RANK[spec["required_degree"]]
    )
    role_ok = candidate["role_family"] == spec["role_family"]

    why = {
        "must_have_matched": sorted(must_hits),
        "must_have_missing": sorted(must - skills),
        "nice_to_have_matched": sorted(nice_hits),
        "years": candidate["total_years_experience"],
        "years_ok": years_ok,
        "degree_ok": degree_ok,
        "role_family_match": role_ok,
    }

    # Missing a MUST-have caps the grade regardless of everything else. That is
    # what "must" means, and a grading scheme that quietly lets a strong
    # candidate through without a required skill would teach the evaluation to
    # reward the wrong thing.
    if len(must_hits) < len(must):
        grade = 1 if (must_hits and years_ok) else 0
    elif not years_ok or not degree_ok:
        grade = 1
    elif role_ok and len(nice_hits) >= 1:
        grade = 3
    else:
        grade = 2

    return grade, why



# ============================================================
# CALIBRATION
# ============================================================

MIN_RELEVANT = 5


def label_all(spec: dict, candidates: list[dict]) -> dict:
    labels = {}
    for candidate in candidates:
        grade, why = grade_candidate(candidate, spec)
        if grade > 0:
            labels[candidate["resume_id"]] = {"grade": grade, "why": why}
    return labels


def calibrate(spec: dict, candidates: list[dict]) -> dict:
    """Relaxes the JD until enough candidates qualify to measure against.

    A first pass produced JDs with ZERO qualifying candidates: requiring three
    specific skills is too strict when candidates list five to eight from a pool
    of ten. Recall@45 is undefined when nothing is relevant, and an evaluation
    set where most queries have one relevant document measures noise.

    So a JD that nobody matches is treated the way a real one should be -- as
    over-specified -- and its rarest requirement is dropped until at least
    MIN_RELEVANT candidates reach grade 2. The relaxation is recorded on the
    spec, because a JD that had to be relaxed three times is a different kind of
    query from one that did not, and step 13 should be able to see that.
    """
    frequency = {}
    for candidate in candidates:
        for skill in candidate["canonical_skills"]:
            frequency[skill.lower()] = frequency.get(skill.lower(), 0) + 1

    relaxations = 0
    labels = label_all(spec, candidates)

    while (
        sum(1 for v in labels.values() if v["grade"] >= 2) < MIN_RELEVANT
        and len(spec["must_have"]) > 1
    ):
        # Drop the requirement fewest candidates satisfy -- that is the one
        # doing the excluding.
        rarest = min(spec["must_have"], key=lambda s: frequency.get(s.lower(), 0))
        spec["must_have"].remove(rarest)
        if rarest not in spec["nice_to_have"]:
            # It stays in the posting, downgraded. A real hiring manager does
            # the same thing: "must have" becomes "nice to have" rather than
            # disappearing.
            spec["nice_to_have"].append(rarest)
        relaxations += 1
        labels = label_all(spec, candidates)

    spec["relaxations"] = relaxations
    return labels


# ============================================================
# GENERATION
# ============================================================

def build_spec(index: int, role_family: str, rng: random.Random) -> dict:
    role = ROLE_SPECS[role_family]
    must = list(role["must_have"])
    # Occasionally drop a must-have, so not every JD is equally strict and the
    # evaluation sees a range of selectivity.
    if rng.random() < 0.3 and len(must) > 2:
        must.pop(rng.randrange(len(must)))

    return {
        "jd_id": f"jd_{index:03d}",
        "role_family": role_family,
        "title": rng.choice(role["titles"]),
        "company": rng.choice(COMPANIES),
        "location": rng.choice(["Bengaluru", "Remote (India)", "Pune", "Hybrid - Gurugram"]),
        "product": rng.choice([
            "the payments platform", "the internal analytics stack",
            "our customer-facing search", "the recommendations service",
            "the data platform",
        ]),
        "min_years": rng.choice(role["min_years"]),
        "required_degree": rng.choice([None, None, "bachelor", "master"]),
        "must_have": must,
        "nice_to_have": rng.sample(role["nice_to_have"], k=min(3, len(role["nice_to_have"]))),
        "soft_requirements": rng.sample(role["soft_requirements"], k=2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not CANDIDATES_FILE.exists():
        raise SystemExit(
            f"missing {CANDIDATES_FILE} - run generate_sample_resumes.py first"
        )

    rng = random.Random(args.seed)
    candidates = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
    JD_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_FILE.parent.mkdir(parents=True, exist_ok=True)

    families = list(ROLE_SPECS)
    records = []

    for i in range(1, args.count + 1):
        # Cycle through role families so every kind of JD appears, rather than
        # letting random choice produce five backend roles and no analysts.
        spec = build_spec(i, families[(i - 1) % len(families)], rng)
        labels = calibrate(spec, candidates)
        text = render_jd(spec)
        (JD_DIR / f"{spec['jd_id']}.txt").write_text(text, encoding="utf-8")

        spec["labels"] = labels
        spec["n_relevant"] = sum(1 for v in labels.values() if v["grade"] >= 2)
        spec["n_highly_relevant"] = sum(1 for v in labels.values() if v["grade"] == 3)
        records.append(spec)

        print(f"{spec['jd_id']}  {spec['title'][:30]:30} "
              f"{spec['min_years']}y+  "
              f"must={','.join(spec['must_have'])[:34]:34} "
              f"grade3={spec['n_highly_relevant']:2}  "
              f"grade2+={spec['n_relevant']:2}")

    GROUND_TRUTH_FILE.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    total_pairs = len(records) * len(candidates)
    graded = sum(len(r["labels"]) for r in records)
    print(f"\n{len(records)} JDs -> {JD_DIR}")
    print(f"labels -> {GROUND_TRUTH_FILE}")
    print(f"{graded} non-zero labels across {total_pairs} JD-candidate pairs")


if __name__ == "__main__":
    main()