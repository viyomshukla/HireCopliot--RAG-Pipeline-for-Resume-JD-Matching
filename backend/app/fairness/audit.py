"""
Bias audit: does the shortlist select some groups at a lower rate than others?

WHAT DISPARATE IMPACT MEANS
---------------------------
A system can be biased without ever seeing the attribute it discriminates on.
The ranker here never reads a candidate's name locale, their university's
prestige tier, or whether they have a career gap -- none of those are extracted
and none are in the database. And it can still select them at different rates,
because those attributes correlate with things it DOES see: how a resume is
worded, which employers appear, how continuous the dates are.

That is why "we don't use protected attributes" is not a fairness claim. The
only way to know is to measure the outcome.

THE FOUR-FIFTHS RULE
--------------------
The standard US test (EEOC Uniform Guidelines, adopted 1978):

    selection rate = shortlisted in group / total in group
    impact ratio   = lowest group's rate / highest group's rate

A ratio below 0.80 is treated as evidence of adverse impact and requires
justification. It is a screening heuristic, not proof of discrimination -- but it
is the number regulators and auditors actually use, so it is the number to
report.

Relevant context for a hiring tool: NYC Local Law 144 requires an annual
independent bias audit of automated employment decision tools and publication of
the results. The EU AI Act classifies employment screening as high-risk. An audit
is not an optional extra for this category of system.

WHAT THIS AUDIT CANNOT TELL YOU
--------------------------------
Three honest limits, all worth stating in a README rather than glossing:

  SAMPLE SIZE   With 50 candidates split into groups of 6-15, one person moving
                across the shortlist boundary swings a selection rate by 10-15
                points. Ratios computed on small groups are noisy, and this file
                flags them rather than reporting them as findings.

  CORRELATION   A low ratio does not prove the system caused it. If one group
                genuinely has less experience in this corpus, a legitimate
                years filter will select them less. Distinguishing unfair
                treatment from a real difference in qualifications needs
                controls this audit does not have.

  PROXIES ONLY  The attributes here are stand-ins recorded by the generator, not
                real protected characteristics. They demonstrate the METHOD.
                Auditing a real system needs real demographic data, collected
                with consent and handled under far stricter rules.

WHY THE ATTRIBUTES LIVE IN THE GROUND TRUTH FILE
-------------------------------------------------
The ranker must not be able to read them -- a system that can see the attribute
can be tuned to game the audit. They sit in candidates.json, which no part of the
pipeline loads. Only this file does.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

BASE = Path(__file__).resolve().parents[2]
GROUND_TRUTH_FILE = BASE / "data" / "ground_truth" / "candidates.json"

FOUR_FIFTHS = 0.80

# Below this, a group's selection rate is too noisy to draw conclusions from.
# 30 is the usual rule of thumb for a proportion; at 50 candidates nothing will
# reach it, which is itself the finding worth reporting.
MIN_GROUP_SIZE = 10

# Attributes the ranker never sees. Each is a documented proxy for something a
# real audit would measure directly.
PROXY_ATTRIBUTES = {
    "name_locale": "name origin (proxy for national origin)",
    "university_tier": "institution prestige (proxy for socioeconomic background)",
    "has_career_gap": "career continuity (proxy for caregiving, illness, redundancy)",
    "currently_employed": "employment status (proxy for recency bias)",
}


@dataclass
class GroupResult:
    value: str
    total: int
    selected: int
    mean_score: float

    @property
    def selection_rate(self) -> float:
        return self.selected / self.total if self.total else 0.0

    @property
    def reliable(self) -> bool:
        return self.total >= MIN_GROUP_SIZE


@dataclass
class AttributeAudit:
    attribute: str
    description: str
    groups: list[GroupResult]

    @property
    def impact_ratio(self) -> Optional[float]:
        """Lowest selection rate divided by highest. None when undefined.

        Groups too small to be reliable are EXCLUDED from the ratio rather than
        dragging it down -- a group of three where nobody was selected produces a
        ratio of 0.0 that means nothing except that the group was small.
        """
        rates = [g.selection_rate for g in self.groups if g.reliable]
        if len(rates) < 2 or max(rates) == 0:
            return None
        return min(rates) / max(rates)

    @property
    def flagged(self) -> bool:
        ratio = self.impact_ratio
        return ratio is not None and ratio < FOUR_FIFTHS

    @property
    def unreliable_groups(self) -> list[GroupResult]:
        return [g for g in self.groups if not g.reliable]


def load_attributes() -> dict[str, dict]:
    """resume_id -> proxy attributes. The only place these are read."""
    if not GROUND_TRUTH_FILE.exists():
        raise FileNotFoundError(
            f"{GROUND_TRUTH_FILE} not found - the audit needs the generator's "
            f"recorded attributes"
        )
    records = json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))
    return {r["resume_id"]: r["proxy_attributes"] for r in records}


def audit_shortlist(
    ranked: list,
    shortlist_size: int,
    attributes: Optional[dict[str, dict]] = None,
) -> list[AttributeAudit]:
    """Audits one shortlist.

    `ranked` is the scorer's output, best-first. The shortlist is the top N of
    those who passed the hard filter -- candidates excluded by hard requirements
    count as NOT SELECTED, not as absent. Excluding them would hide the most
    likely source of disparate impact, since a hard filter is exactly where a
    whole group can be removed at once.
    """
    attributes = attributes or load_attributes()

    qualified = [c for c in ranked if c.passed_filter]
    selected = {c.resume_id for c in qualified[:shortlist_size]}
    scores = {c.resume_id: c.score for c in ranked}

    audits = []
    for attribute, description in PROXY_ATTRIBUTES.items():
        buckets: dict[str, list[str]] = defaultdict(list)
        for candidate in ranked:
            attrs = attributes.get(candidate.resume_id)
            if not attrs or attribute not in attrs:
                continue
            buckets[str(attrs[attribute])].append(candidate.resume_id)

        groups = []
        for value, resume_ids in sorted(buckets.items()):
            picked = sum(1 for r in resume_ids if r in selected)
            mean = (
                sum(scores.get(r, 0.0) for r in resume_ids) / len(resume_ids)
                if resume_ids else 0.0
            )
            groups.append(GroupResult(value, len(resume_ids), picked, mean))

        audits.append(AttributeAudit(attribute, description, groups))

    return audits


def format_audit(audits: list[AttributeAudit], shortlist_size: int, total: int) -> str:
    lines = [
        f"BIAS AUDIT   shortlist {shortlist_size} of {total} candidates",
        "",
        "The ranker never sees any attribute below. They are recorded by the",
        "generator and read only here. Disparate impact is measured on the",
        "OUTCOME, because a system can discriminate without seeing the attribute.",
        "",
    ]

    for audit in audits:
        ratio = audit.impact_ratio
        if ratio is None:
            verdict = "not enough data"
        elif audit.flagged:
            verdict = f"FLAGGED  impact ratio {ratio:.2f} < {FOUR_FIFTHS}"
        else:
            verdict = f"ok       impact ratio {ratio:.2f}"

        lines.append(f"{audit.attribute}  ({audit.description})")
        lines.append(f"   {verdict}")
        lines.append(f"   {'group':<18}{'n':>4}{'selected':>10}{'rate':>8}{'mean score':>12}")

        for g in sorted(audit.groups, key=lambda g: -g.selection_rate):
            note = "" if g.reliable else "   (too small to interpret)"
            lines.append(
                f"   {g.value[:18]:<18}{g.total:>4}{g.selected:>10}"
                f"{g.selection_rate:>8.2f}{g.mean_score:>12.3f}{note}"
            )
        lines.append("")

    flagged = [a for a in audits if a.flagged]
    unreliable = sum(len(a.unreliable_groups) for a in audits)

    lines.append("-" * 60)
    if flagged:
        lines.append(f"{len(flagged)} attribute(s) below the four-fifths threshold: "
                     f"{', '.join(a.attribute for a in flagged)}")
        lines.append("A flag is a prompt to investigate, not a verdict. Check whether")
        lines.append("the difference is explained by a legitimate requirement before")
        lines.append("changing anything.")
    else:
        lines.append("No attribute fell below the four-fifths threshold.")

    if unreliable:
        lines.append("")
        lines.append(f"{unreliable} group(s) had fewer than {MIN_GROUP_SIZE} members and were")
        lines.append("excluded from the ratios. At this corpus size one person crossing")
        lines.append("the shortlist boundary moves a rate by 10-15 points, so small-group")
        lines.append("numbers are shown for transparency, not as findings.")
    return "\n".join(lines)


def audit_across_jds(results_by_jd: dict[str, list], shortlist_size: int) -> str:
    """Pools every JD into one audit.

    A single shortlist of 10 is far too small to conclude anything. Pooling
    eight of them gives 80 selection decisions, which is still modest but is the
    right direction -- and it is how a real audit works, over many hiring rounds
    rather than one.
    """
    attributes = load_attributes()
    pooled: dict[str, dict[str, list[int]]] = {
        a: defaultdict(lambda: [0, 0]) for a in PROXY_ATTRIBUTES
    }

    for ranked in results_by_jd.values():
        qualified = [c for c in ranked if c.passed_filter]
        selected = {c.resume_id for c in qualified[:shortlist_size]}
        for candidate in ranked:
            attrs = attributes.get(candidate.resume_id)
            if not attrs:
                continue
            for attribute in PROXY_ATTRIBUTES:
                if attribute not in attrs:
                    continue
                bucket = pooled[attribute][str(attrs[attribute])]
                bucket[0] += 1
                bucket[1] += 1 if candidate.resume_id in selected else 0

    lines = [
        f"POOLED AUDIT   {len(results_by_jd)} job descriptions, "
        f"top {shortlist_size} each",
        "",
    ]
    for attribute, description in PROXY_ATTRIBUTES.items():
        groups = [
            GroupResult(value, total, picked, 0.0)
            for value, (total, picked) in sorted(pooled[attribute].items())
        ]
        audit = AttributeAudit(attribute, description, groups)
        ratio = audit.impact_ratio
        verdict = (
            "not enough data" if ratio is None
            else f"FLAGGED  {ratio:.2f}" if audit.flagged
            else f"ok  {ratio:.2f}"
        )
        lines.append(f"{attribute:<22} {verdict}")
        for g in sorted(groups, key=lambda g: -g.selection_rate):
            mark = "" if g.reliable else "  (small)"
            lines.append(f"   {g.value[:18]:<18}{g.selected:>4}/{g.total:<4}"
                         f"{g.selection_rate:>7.2f}{mark}")
        lines.append("")
    return "\n".join(lines)