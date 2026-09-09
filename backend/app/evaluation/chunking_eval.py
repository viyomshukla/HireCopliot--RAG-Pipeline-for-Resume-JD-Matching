"""
Chunking evaluation: scores chunks.json against the generator's ground truth.

WHY THIS FILE EXISTS
--------------------
Chunking has no obvious right answer, which makes it very easy to change the
chunker, glance at some output, decide it "looks fine", and ship a regression.
Every failure this project has hit so far was invisible to eyeballing:

  * DOCX job titles glued onto the previous job's chunk
  * PROJECTS never splitting, because project titles contain no dates
  * an entire EXPERIENCE section vanishing when a heading absorbed a date line

All three produced plausible-looking JSON. Only a comparison against known
answers caught them.

The generator wrote candidates.json from the SAME records it rendered the PDFs
from, so we know exactly how many jobs, degrees and projects each resume really
has. That turns "does this look right" into a number.

Run this after every change to the parser or the chunker. If a number drops,
you broke something.

WHAT IT MEASURES
----------------
  1. Entry-split accuracy  -- did we find the right NUMBER of jobs/degrees/projects?
  2. Citation integrity    -- does any chunk mix evidence from two employers?
  3. Content completeness  -- did any resume text get lost entirely?
  4. Attribution           -- can every chunk be traced to a candidate?
  5. Section coverage      -- did every section in the document get recognised?
  6. Size distribution     -- are chunks in a sane range for retrieval?

WHAT IT DOES NOT MEASURE
------------------------
Whether the chunks are GOOD FOR RETRIEVAL. Counting entries correctly is
necessary but not sufficient -- a chunk can be perfectly bounded and still
retrieve badly. That question needs queries and relevance labels, and it gets
answered later by the retrieval evaluation (step 13). This file checks
structural correctness only, which is the thing that has to be right first.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
CHUNKS_FILE = BASE / "data" / "chunks.json"
GROUND_TRUTH_FILE = BASE / "data" / "ground_truth" / "candidates.json"

# Sections whose entries we can count against ground truth. CERTIFICATIONS and
# ACHIEVEMENTS are deliberately excluded: they are short one-line items, and
# grouping several into one chunk is fine (arguably better) rather than a bug.
COUNTABLE = {
    "EXPERIENCE": "experience",
    "EDUCATION": "education",
    "PROJECTS": "projects",
}

# A chunk far below this is usually a stray fragment; far above it dilutes its
# own embedding. Not pass/fail, just something to look at.
SANE_MIN_CHARS = 40
SANE_MAX_CHARS = 1200


def load() -> tuple[list[dict], dict[str, dict]]:
    if not CHUNKS_FILE.exists():
        sys.exit(f"missing {CHUNKS_FILE} - run the chunker first")
    if not GROUND_TRUTH_FILE.exists():
        sys.exit(f"missing {GROUND_TRUTH_FILE} - run the generator first")

    chunks = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    truth = {c["resume_id"]: c for c in json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))}
    return chunks, truth


def index_chunks(chunks: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Groups chunks by (resume_id, section) so lookups below stay cheap."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in chunks:
        key = (c["metadata"]["resume_id"], c["metadata"]["section"])
        grouped[key].append(c)
    return grouped


def entries_found(grouped, resume_id: str, section: str) -> int:
    """How many distinct entries the chunker believes this section has.

    item_index counts entries; part_index counts the pieces an oversized entry
    was split into. Taking the max of item_index means a job split across two
    chunks still counts as ONE job, which is what we want to compare.
    """
    got = grouped.get((resume_id, section), [])
    return max((c["metadata"]["item_index"] for c in got), default=0)


def report_entry_accuracy(grouped, truth) -> dict[str, float]:
    print("1. ENTRY-SPLIT ACCURACY")
    print("   did the chunker find the right number of items per section?\n")
    scores = {}

    for section, gt_key in COUNTABLE.items():
        exact = under = over = 0
        failures = []

        for resume_id, record in truth.items():
            expected = len(record.get(gt_key, []))
            # A section the candidate simply does not have is not a miss.
            if expected == 0 or section not in record["format"]["section_order"]:
                continue

            got = entries_found(grouped, resume_id, section)
            if got == expected:
                exact += 1
            else:
                (under if got < expected else over).__class__  # no-op, keep flake quiet
                if got < expected:
                    under += 1
                else:
                    over += 1
                failures.append((resume_id, expected, got, record))

        total = exact + under + over
        pct = (exact / total * 100) if total else 100.0
        scores[section] = pct
        print(f"   {section:12} {exact:3}/{total:<3} exact  ({pct:5.1f}%)   "
              f"under-split {under}, over-split {over}")

        # Print the FORMAT of each failure, not just the id. Failures cluster by
        # layout or date style, and seeing that cluster is usually the whole
        # diagnosis -- 11 failures all sharing date_style=slash pointed straight
        # at a regex that only accepted 4-digit years.
        for resume_id, expected, got, record in failures[:5]:
            fmt = record["format"]
            print(f"        {resume_id}  expected {expected}, got {got}  "
                  f"[{record['file_format']}, {fmt['title_date_layout']}, "
                  f"{fmt['date_style']}, {fmt['heading_style']}]")
        if len(failures) > 5:
            print(f"        ... and {len(failures) - 5} more")

        if failures:
            clusters = Counter(
                (r["file_format"], r["format"]["title_date_layout"], r["format"]["date_style"])
                for _, _, _, r in failures
            )
            top, n = clusters.most_common(1)[0]
            if n >= 3:
                print(f"        pattern: {n}/{len(failures)} failures share {top}")
    print()
    return scores


def report_citation_integrity(chunks, truth) -> int:
    """A chunk must never contain evidence belonging to two different employers.

    This is the check that matters most for a hiring tool. If a chunk holds
    Google's bullets and Microsoft's heading, then any evidence shown to a
    recruiter is misattributed -- and the ranking is built on it too.
    """
    print("2. CITATION INTEGRITY")
    bad = []
    for c in chunks:
        meta = c["metadata"]
        if meta["section"] != "EXPERIENCE":
            continue
        record = truth.get(meta["resume_id"])
        if not record:
            continue
        employers = {j["company"] for j in record["experience"] if j["company"] in c["text"]}
        if len(employers) > 1:
            bad.append((c["chunk_id"], sorted(employers)))

    if bad:
        print(f"   FAIL  {len(bad)} chunks name two or more employers")
        for chunk_id, employers in bad[:5]:
            print(f"        {chunk_id}: {', '.join(employers)}")
    else:
        print("   ok    no chunk mixes evidence from two employers")
    print()
    return len(bad)


def report_completeness(chunks, truth) -> int:
    """Nothing the candidate wrote may disappear.

    Over-eager boundary logic can drop lines silently: an entry that never gets
    flushed, a heading that swallows content. Checking that every ground-truth
    responsibility still appears SOMEWHERE catches that class of bug, which the
    entry counts alone would not.
    """
    print("3. CONTENT COMPLETENESS")
    text_by_resume: dict[str, str] = defaultdict(str)
    for c in chunks:
        text_by_resume[c["metadata"]["resume_id"]] += "\n" + c["text"]

    missing = 0
    affected = set()
    for resume_id, record in truth.items():
        blob = text_by_resume.get(resume_id, "")
        for job in record["experience"]:
            for line in job["responsibilities"]:
                if line not in blob:
                    missing += 1
                    affected.add(resume_id)

    total = sum(len(j["responsibilities"]) for r in truth.values() for j in r["experience"])
    if missing:
        print(f"   FAIL  {missing}/{total} responsibility lines lost "
              f"across {len(affected)} resumes")
        print(f"        e.g. {sorted(affected)[:5]}")
    else:
        print(f"   ok    all {total} responsibility lines present in the chunks")
    print()
    return missing


def report_attribution(chunks) -> int:
    print("4. ATTRIBUTION")
    orphans = [c["chunk_id"] for c in chunks if not c["metadata"].get("candidate_name")]
    if orphans:
        print(f"   FAIL  {len(orphans)} chunks have no candidate name attached")
        print(f"        e.g. {orphans[:5]}")
    else:
        print(f"   ok    all {len(chunks)} chunks carry a candidate name")
    print()
    return len(orphans)


def report_section_coverage(grouped, truth) -> int:
    """Every section the resume actually rendered should have been recognised.

    A section that lands in OTHER, or vanishes entirely, means the heading was
    not matched -- usually an alias worth adding to CANONICAL_SECTIONS.
    """
    print("5. SECTION COVERAGE")
    misses: list[str] = []
    for resume_id, record in truth.items():
        for section in record["format"]["section_order"]:
            if entries_found(grouped, resume_id, section) == 0 and not grouped.get((resume_id, section)):
                misses.append(f"{resume_id}:{section}")

    other = sum(len(v) for k, v in grouped.items() if k[1] == "OTHER")
    if misses:
        print(f"   FAIL  {len(misses)} sections rendered but not recognised")
        print(f"        e.g. {misses[:6]}")
    else:
        print("   ok    every rendered section was recognised")
    print(f"   note  {other} chunks in OTHER (unrecognised headings - expected, "
          f"the generator emits some on purpose)")
    print()
    return len(misses)


def report_sizes(chunks) -> None:
    print("6. CHUNK SIZE DISTRIBUTION")
    sizes = sorted(c["metadata"]["char_count"] for c in chunks)
    if not sizes:
        print("   no chunks\n")
        return

    def pct(p: float) -> int:
        return sizes[min(len(sizes) - 1, int(len(sizes) * p))]

    tiny = sum(1 for s in sizes if s < SANE_MIN_CHARS)
    huge = sum(1 for s in sizes if s > SANE_MAX_CHARS)
    print(f"   count {len(sizes)}   min {sizes[0]}   p50 {int(statistics.median(sizes))}   "
          f"p90 {pct(0.9)}   max {sizes[-1]}")
    print(f"   below {SANE_MIN_CHARS} chars: {tiny}   above {SANE_MAX_CHARS} chars: {huge}")
    per_section = Counter(c["metadata"]["section"] for c in chunks)
    print("   by section: " + ", ".join(f"{k} {v}" for k, v in sorted(per_section.items())))
    print()


def main() -> None:
    chunks, truth = load()
    grouped = index_chunks(chunks)

    print("=" * 64)
    print(f"CHUNKING EVALUATION   {len(chunks)} chunks from {len(truth)} resumes")
    print("=" * 64 + "\n")

    scores = report_entry_accuracy(grouped, truth)
    problems = 0
    problems += report_citation_integrity(chunks, truth)
    problems += report_completeness(chunks, truth)
    problems += report_attribution(chunks)
    problems += report_section_coverage(grouped, truth)
    report_sizes(chunks)

    worst = min(scores.values()) if scores else 0.0
    print("=" * 64)
    print(f"entry accuracy: " + "  ".join(f"{k} {v:.0f}%" for k, v in scores.items()))
    print(f"hard failures : {problems}")
    print("=" * 64)

    # Non-zero exit on regression, so this can gate a commit or a CI run later.
    if problems or worst < 100.0:
        sys.exit(1)


if __name__ == "__main__":
    main()