"""
Extraction evaluation: scores extractions.json against the generator's ground truth.

WHY THIS IS THE MOST IMPORTANT EVALUATION IN THE PROJECT
--------------------------------------------------------
Chunking errors are visible if you look: a chunk is obviously cut in the wrong
place. Extraction errors are INVISIBLE. The JSON comes back perfectly formed,
every field populated, every type correct -- whether the model read the resume
accurately or quietly invented half of it. Validation proves the SHAPE is right.
It says nothing about whether the CONTENT is true.

That matters more here than in most RAG projects, because these fields become
hard filters. If years-of-experience is wrong for 20% of candidates, then
`WHERE total_experience_months >= 60` silently rejects good people, and you will
never trace the bad shortlist back to a date-parsing mistake three stages
upstream.

WHAT IT MEASURES, AND WHY EACH ONE
----------------------------------
  1. Coverage      -- did every resume produce a record at all?
  2. Identity      -- name, email, phone. Cheap to check, and a wrong name means
                      the wrong person gets contacted.
  3. Skills        -- precision AND recall. Recall matters because a missed
                      skill silently fails a requirement. Precision matters
                      because an invented skill is worse: it puts an unqualified
                      candidate on the shortlist.
  4. Experience    -- companies matched, dates correct, and total years, which
                      is the single most-used hard filter.
  5. Education     -- degree level, which drives another common filter.
  6. Verbatim      -- did the model COPY the bullets or rewrite them? We told it
                      to copy. Rewritten bullets destroy the evidence a
                      recruiter is shown, and no schema validator can catch it.
  7. Hallucination -- employers or skills that appear nowhere in the resume.
                      This is the failure that most damages trust in the system,
                      so it is reported separately rather than folded into a
                      precision score.

PRECISION VS RECALL, IN ONE LINE EACH
-------------------------------------
  Recall    = of the things that were really there, how many did we find?
  Precision = of the things we claimed, how many were really there?
A model that lists every skill it can imagine has perfect recall and terrible
precision. One that lists nothing has perfect precision and zero recall. You
need both numbers; either alone is easy to game.

A NOTE ON THE SKILL MATCHING BELOW
----------------------------------
The generator deliberately writes skills in varied surface forms -- "Torch" for
PyTorch, "advanced SQL" for SQL. So comparing extracted text to ground truth
character-by-character would score a correct extraction as wrong. We therefore
build a surface -> canonical map FROM the ground truth file itself.

That is legitimate here because we are measuring the LLM, not the normaliser.
But be clear about what it means: in production no such map falls out of the
sky. You would need a curated skill vocabulary, which is exactly the
`canonical` field left empty in schemas.py, and exactly why step 8 exists.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
EXTRACTIONS_FILE = BASE / "data" / "extractions.json"
GROUND_TRUTH_FILE = BASE / "data" / "ground_truth" / "candidates.json"

# How close a computed years-of-experience has to be to count as correct.
# Exact-to-the-month is an unfair bar: a resume saying "2021 - 2023" genuinely
# does not specify months, so any reader has to round. One year of slack
# measures whether the model read the DATES right, not whether it guessed the
# same rounding we did.
YEARS_TOLERANCE = 1.0


# ============================================================
# NORMALISATION
# ============================================================

def norm(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace.

    Keeps + and # so 'C++' and 'C#' survive as distinct skills. Without that,
    both collapse to 'c' and match each other.
    """
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9+#. ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_surface_map(truth: list[dict]) -> dict[str, str]:
    """surface form -> canonical name, learned from the ground truth pairs.

    See the module docstring: this is a measurement convenience, not something
    you would have in production.
    """
    mapping: dict[str, str] = {}
    for record in truth:
        for canonical, surface in zip(record["canonical_skills"], record["surface_skills"]):
            mapping[norm(surface)] = canonical
            mapping[norm(canonical)] = canonical
    return mapping


def canonicalise(skill: str, surface_map: dict[str, str]) -> str:
    """Best effort mapping of one extracted skill onto a canonical name."""
    n = norm(skill)
    if n in surface_map:
        return surface_map[n]

    # Fall back to containment, so "Python 3" and "PyTorch (training)" still
    # match "Python" and "PyTorch". Longest match wins, otherwise "SQL" would
    # match before "advanced SQL" and lose the more specific mapping.
    best, best_len = None, 0
    for surface, canonical in surface_map.items():
        if len(surface) > best_len and (surface in n or n in surface):
            best, best_len = canonical, len(surface)
    return best or n


def companies_equal(a: str, b: str) -> bool:
    """Company names match if one contains the other after normalisation.

    'Amazon' vs 'Amazon India' vs 'Amazon Web Services' should count as the
    same employer for our purposes. Being strict here would report failures
    that are really just formatting.
    """
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


# ============================================================
# METRICS
# ============================================================

def prf(true_positives: int, predicted: int, actual: int) -> tuple[float, float, float]:
    precision = true_positives / predicted if predicted else 0.0
    recall = true_positives / actual if actual else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def pct(n: int, d: int) -> str:
    return f"{(n / d * 100):5.1f}%" if d else "    -"


# ============================================================
# EVALUATION
# ============================================================

def evaluate(records: list[dict], truth_by_id: dict[str, dict], surface_map) -> None:
    scored = [r for r in records if r["metadata"]["resume_id"] in truth_by_id]

    print("=" * 68)
    print(f"EXTRACTION EVALUATION   {len(scored)} records vs ground truth")
    print("=" * 68 + "\n")

    # ---------- identity ----------
    name_ok = 0
    for r in scored:
        got = norm(r["profile"].get("full_name") or "")
        want = norm(truth_by_id[r["metadata"]["resume_id"]]["name"])
        if got == want:
            name_ok += 1

    has_email = sum(1 for r in scored if r["profile"].get("email"))
    has_phone = sum(1 for r in scored if r["profile"].get("phone"))

    print("1. IDENTITY")
    print(f"   name exactly right : {pct(name_ok, len(scored))}  ({name_ok}/{len(scored)})")
    print(f"   email present      : {pct(has_email, len(scored))}")
    print(f"   phone present      : {pct(has_phone, len(scored))}\n")

    # ---------- skills ----------
    tp = fp = fn = 0
    invented: Counter = Counter()
    missed: Counter = Counter()

    for r in scored:
        gt = truth_by_id[r["metadata"]["resume_id"]]
        want = {norm(s) for s in gt["canonical_skills"]}
        got = {norm(canonicalise(s["name"], surface_map)) for s in r["profile"].get("skills", [])}

        hits = want & got
        tp += len(hits)
        fp += len(got - want)
        fn += len(want - got)
        invented.update(got - want)
        missed.update(want - got)

    p, rc, f1 = prf(tp, tp + fp, tp + fn)
    print("2. SKILLS")
    print(f"   precision {p:5.1%}   recall {rc:5.1%}   f1 {f1:5.1%}")
    print(f"   found {tp}, missed {fn}, extra {fp}")
    if missed:
        print(f"   most missed  : {', '.join(s for s, _ in missed.most_common(5))}")
    if invented:
        # "Extra" is not automatically wrong: the generator plants skills inside
        # job bullets that are not in the candidate's declared skill list, and
        # we asked the model to pick those up. Worth reading rather than
        # treating as an error count.
        print(f"   most extra   : {', '.join(s for s, _ in invented.most_common(5))}")
    print()

    # ---------- experience ----------
    jobs_expected = jobs_matched = 0
    start_ok = end_ok = dated = 0
    hallucinated_companies: list[tuple[str, str]] = []
    count_exact = 0

    for r in scored:
        gt = truth_by_id[r["metadata"]["resume_id"]]
        got_jobs = r["profile"].get("experience", [])
        want_jobs = gt["experience"]

        jobs_expected += len(want_jobs)
        if len(got_jobs) == len(want_jobs):
            count_exact += 1

        used = set()
        for want in want_jobs:
            for i, got in enumerate(got_jobs):
                if i in used or not companies_equal(got.get("company") or "", want["company"]):
                    continue
                used.add(i)
                jobs_matched += 1
                dated += 1
                if got.get("start_year") == want["start_year"]:
                    start_ok += 1
                # A current role has no end year in either place, which counts
                # as agreement.
                if got.get("end_year") == want["end_year"]:
                    end_ok += 1
                break

        # Any extracted employer that matches nothing in the resume is an
        # invention, and inventions are reported by name -- a rate alone hides
        # whether it is one weird resume or a systemic prompt problem.
        for i, got in enumerate(got_jobs):
            if i not in used:
                hallucinated_companies.append(
                    (r["metadata"]["resume_id"], got.get("company") or "?")
                )

    print("3. EXPERIENCE")
    print(f"   right number of jobs : {pct(count_exact, len(scored))}")
    print(f"   employers matched    : {pct(jobs_matched, jobs_expected)}  "
          f"({jobs_matched}/{jobs_expected})")
    print(f"   start year correct   : {pct(start_ok, dated)}")
    print(f"   end year correct     : {pct(end_ok, dated)}")
    if hallucinated_companies:
        print(f"   INVENTED EMPLOYERS   : {len(hallucinated_companies)}")
        for rid, company in hallucinated_companies[:5]:
            print(f"        {rid}: '{company}'")
    else:
        print("   invented employers   : none")
    print()

    # ---------- years of experience ----------
    close = exact = 0
    worst: list[tuple[str, float, float]] = []
    for r in scored:
        gt = truth_by_id[r["metadata"]["resume_id"]]
        # Compute BOTH sides with the same function, from the same raw dates.
        # candidates.json stores years as floor division, so comparing against
        # that field directly reports a systematic half-year error that is the
        # evaluator's rounding, not the model's mistake. Measure like against
        # like, or you spend an afternoon debugging a bug you invented.
        want = compute_years(gt)
        got = compute_years(r["profile"])
        if abs(got - want) < 0.05:
            exact += 1
        if abs(got - want) <= YEARS_TOLERANCE:
            close += 1
        else:
            worst.append((r["metadata"]["resume_id"], want, got))

    print("4. TOTAL YEARS OF EXPERIENCE   (drives the most common hard filter)")
    print(f"   exact                : {pct(exact, len(scored))}")
    print(f"   within {YEARS_TOLERANCE:.0f} year        : {pct(close, len(scored))}")
    for rid, want, got in sorted(worst, key=lambda w: abs(w[1] - w[2]), reverse=True)[:5]:
        print(f"        {rid}: truth {want:.0f}y, extracted {got:.1f}y")
    print()

    # ---------- education ----------
    level_ok = count_ok = 0
    for r in scored:
        gt = truth_by_id[r["metadata"]["resume_id"]]
        got_edu = r["profile"].get("education", [])
        if len(got_edu) == len(gt["education"]):
            count_ok += 1
        want_level = gt["highest_degree_level"]  # 'undergraduate' | 'postgraduate'
        got_levels = {e.get("level") for e in got_edu}
        got_level = "postgraduate" if got_levels & {"master", "doctorate"} else "undergraduate"
        if got_level == want_level:
            level_ok += 1

    print("5. EDUCATION")
    print(f"   right number of entries : {pct(count_ok, len(scored))}")
    print(f"   highest level correct   : {pct(level_ok, len(scored))}\n")

    # ---------- verbatim bullets ----------
    # We instructed the model to copy bullets exactly. If it paraphrases, the
    # "evidence" shown to a recruiter is no longer what the candidate wrote --
    # which is a correctness problem and arguably a legal one. No validator can
    # detect this, so it has to be measured.
    total_bullets = kept = 0
    for r in scored:
        gt = truth_by_id[r["metadata"]["resume_id"]]
        got_text = {
            norm(b)
            for job in r["profile"].get("experience", [])
            for b in job.get("responsibilities", [])
        }
        for job in gt["experience"]:
            for bullet in job["responsibilities"]:
                total_bullets += 1
                if norm(bullet) in got_text:
                    kept += 1

    print("6. BULLETS COPIED VERBATIM")
    print(f"   preserved exactly : {pct(kept, total_bullets)}  ({kept}/{total_bullets})")
    if total_bullets and kept / total_bullets < 0.9:
        print("   WARNING: the model is rewriting bullets. Evidence shown to a")
        print("            recruiter would not match the actual resume.")
    print()

    # ---------- flags ----------
    flagged = [r for r in scored if r["metadata"].get("issues")]
    print("7. SELF-REPORTED COMPLETENESS ISSUES")
    if flagged:
        counts = Counter(i for r in flagged for i in r["metadata"]["issues"])
        for issue, n in counts.most_common():
            print(f"   {n:3}  {issue}")
    else:
        print("   none")
    print()

    print("=" * 68)
    print(f"skills f1 {f1:.0%}   employers {pct(jobs_matched, jobs_expected).strip()}   "
          f"years±{YEARS_TOLERANCE:.0f} {pct(close, len(scored)).strip()}   "
          f"verbatim {pct(kept, total_bullets).strip()}")
    print("=" * 68)


def compute_years(profile: dict) -> float:
    """Recomputes total experience from the extracted dates.

    Deliberately duplicates the logic in schemas.py rather than importing it.
    An evaluation that calls the same function it is grading would agree with
    itself by construction. Here we work from the RAW extracted json, so a bug
    in the schema's merging would show up as a discrepancy rather than hide.
    """
    spans = []
    for job in profile.get("experience", []):
        sy = job.get("start_year")
        if sy is None:
            continue
        start = sy * 12 + (job.get("start_month") or 1) - 1
        if job.get("is_current"):
            from datetime import date
            today = date.today()
            end = today.year * 12 + today.month - 1
        elif job.get("end_year"):
            end = job["end_year"] * 12 + (job.get("end_month") or 1) - 1
        else:
            continue
        if end > start:
            spans.append((start, end))

    if not spans:
        return 0.0
    spans.sort()
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return round(sum(e - s for s, e in merged) / 12, 1)


def main() -> None:
    if not EXTRACTIONS_FILE.exists():
        sys.exit(f"missing {EXTRACTIONS_FILE} - run scripts/run_extraction.py first")
    if not GROUND_TRUTH_FILE.exists():
        sys.exit(f"missing {GROUND_TRUTH_FILE} - run the generator first")

    records = json.loads(EXTRACTIONS_FILE.read_text(encoding="utf-8"))
    truth = json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))
    truth_by_id = {t["resume_id"]: t for t in truth}

    evaluate(records, truth_by_id, build_surface_map(truth))


if __name__ == "__main__":
    main()