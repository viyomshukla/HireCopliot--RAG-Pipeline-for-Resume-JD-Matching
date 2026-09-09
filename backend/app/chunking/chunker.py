"""
Section-aware resume chunker for the Recruiter / Hiring Copilot RAG project.

WHAT CHUNKING IS AND WHY IT MATTERS
-----------------------------------
A vector store does not search documents, it searches *chunks*. Whatever you
split the resume into becomes the smallest unit that can be retrieved and shown
to a recruiter as evidence. So chunk boundaries decide two things:

  1. RETRIEVAL PRECISION. A chunk holding an entire resume matches every query
     weakly. A chunk holding one line matches sharply but carries no context.
  2. CITATION INTEGRITY. If a chunk contains bullets from the Google job AND
     the heading of the Microsoft job, then any evidence you show the recruiter
     is wrong. For a hiring tool that is not a cosmetic bug.

The unit that matters for a resume is ONE ENTRY: one job, one degree, one
project. That is what a recruiter reasons about, so that is what we chunk to.

THE THREE STRATEGIES, AND WHY WE PICKED THIS ONE
------------------------------------------------
  * FIXED-SIZE (e.g. every 512 characters, 50 overlap). Ignores meaning
    entirely; slices mid-sentence and mid-job. Cheap, universal, and the wrong
    fit here because resumes have strong explicit structure we would be
    throwing away.
  * SEMANTIC (embed each sentence, cut where embeddings diverge). Clever, but
    it costs an embedding call per sentence and resume bullets inside one job
    are already semantically diverse, so it splits jobs apart.
  * SECTION + ENTRY AWARE (this file). Uses the structure the document already
    declares. Best fit, but it depends on the parser preserving that structure,
    which is the whole design point below.

THE KEY DESIGN DECISION
-----------------------
The parser does NOT return a plain string. It returns one Line record per line,
carrying is_bullet / is_bold / is_heading / font_size. python-docx already knows
a paragraph's style is "List Bullet"; pdfplumber already knows a word's font is
DejaVuSans-Bold at 12pt. An earlier version of this file flattened all of that
to text and then tried to rebuild it with regex on dates and degrees. That fails
in ways that are easy to miss:

  * DOCX job titles have no date on their line, so no boundary fired and each
    job's title got glued onto the PREVIOUS job's chunk.
  * Project titles contain no date and no degree at all, so PROJECTS never
    split -- every project landed in one chunk.
  * Resumes using "01/2018 - 09/2019" failed a regex expecting 4-digit years on
    both sides, which alone broke 11 of 50 resumes.

Regex on dates is now a WEAK SUPPORTING HINT, not the boundary rule. Structure
is the boundary rule.

PRODUCTION vs LEARNING SHORTCUT
------------------------------
Production systems use layout-aware models (LayoutLMv3, Unstructured.io, Azure
Document Intelligence) that read two-column resumes, tables and reading order
properly. This file hand-rolls the cheap version: single-column, top-to-bottom.
It is enough to learn every concept that matters and will struggle on heavily
designed resumes. That is a deliberate trade, not a gap.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


BASE = Path(__file__).resolve().parents[2]
OUTPUT_FILE = BASE / "data" / "chunks.json"
RESUME_DIR = BASE / "data" / "sample_resumes"

# Size caps. NOT driven by the embedding model's limit -- text-embedding-3-small
# accepts ~8000 tokens, far more than any resume entry. These exist for
# RETRIEVAL PRECISION: a large chunk dilutes its own embedding, because the
# vector is an average over everything inside it. One job with 5 bullets is the
# unit we want to match on.
MAX_CHARS_ENTRY = 900
MAX_CHARS_FLAT = 600

# Below this, a "successfully parsed" file is almost certainly a scanned image
# PDF with no text layer. Silently indexing it would rank that candidate last
# forever with no explanation, so we flag it instead.
MIN_DOC_CHARS = 200

BULLET_CHARS = ("\u2022", "\u25aa", "\u25e6", "\u2023", "-", "*", "\u00b7", "\u25cf", "\u2013", "\u2043")


# ============================================================
# CANONICAL SECTIONS
# ============================================================
# Maps the many ways a resume names a section onto one internal name, so
# downstream code filters on "EXPERIENCE" without caring that this particular
# candidate wrote "Employment History".

CANONICAL_SECTIONS = {
    "SUMMARY": "SUMMARY", "PROFESSIONAL SUMMARY": "SUMMARY", "PROFILE": "SUMMARY",
    "OBJECTIVE": "SUMMARY", "CAREER OBJECTIVE": "SUMMARY", "ABOUT ME": "SUMMARY",

    "EXPERIENCE": "EXPERIENCE", "WORK EXPERIENCE": "EXPERIENCE",
    "PROFESSIONAL EXPERIENCE": "EXPERIENCE", "EMPLOYMENT HISTORY": "EXPERIENCE",
    "WORK HISTORY": "EXPERIENCE", "INTERNSHIPS": "EXPERIENCE",
    "RELEVANT EXPERIENCE": "EXPERIENCE",

    "PROJECTS": "PROJECTS", "PERSONAL PROJECTS": "PROJECTS",
    "ACADEMIC PROJECTS": "PROJECTS", "KEY PROJECTS": "PROJECTS",
    "SELECTED PROJECTS": "PROJECTS",

    "EDUCATION": "EDUCATION", "ACADEMIC BACKGROUND": "EDUCATION",
    "ACADEMICS": "EDUCATION", "EDUCATIONAL QUALIFICATIONS": "EDUCATION",
    "QUALIFICATIONS": "EDUCATION",

    "SKILLS": "SKILLS", "TECHNICAL SKILLS": "SKILLS",
    "CORE COMPETENCIES": "SKILLS", "KEY SKILLS": "SKILLS",
    "SKILLS & TOOLS": "SKILLS", "TECHNOLOGIES": "SKILLS",

    "ACHIEVEMENTS": "ACHIEVEMENTS", "HONORS": "ACHIEVEMENTS", "AWARDS": "ACHIEVEMENTS",
    "HONORS & AWARDS": "ACHIEVEMENTS", "AWARDS & HONORS": "ACHIEVEMENTS",
    "ACADEMIC ACHIEVEMENTS": "ACHIEVEMENTS", "ACCOMPLISHMENTS": "ACHIEVEMENTS",

    "PUBLICATIONS": "PUBLICATIONS", "RESEARCH PAPERS": "PUBLICATIONS",
    "RESEARCH & PUBLICATIONS": "PUBLICATIONS", "CONFERENCES": "PUBLICATIONS",
    "RESEARCH": "PUBLICATIONS",

    "CERTIFICATIONS": "CERTIFICATIONS", "CERTIFICATION": "CERTIFICATIONS",
    "LICENSES & CERTIFICATIONS": "CERTIFICATIONS", "COURSES": "CERTIFICATIONS",
}

# Sections where one heading covers SEVERAL independent items, so the state
# machine has to split further. SKILLS and SUMMARY are single blobs and stay flat.
MULTI_ENTRY_SECTIONS = {
    "EXPERIENCE", "EDUCATION", "PROJECTS", "PUBLICATIONS",
}

# Weak supporting hint only. Used to keep a bare date line attached to the title
# above it -- never as the sole reason to start a new entry.
DATE_HINT = re.compile(
    r"\b(?:19|20)\d{2}\b"
    r"|\b(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(?:19|20)\d{2}\b"
    r"|\b(?:Present|Current|Ongoing)\b",
    re.IGNORECASE,
)


# ============================================================
# THE LINE RECORD -- the contract between parser and chunker
# ============================================================

@dataclass
class Line:
    """One visual line of the document, with the formatting the file declared.

    Everything the chunker needs to find boundaries lives here. If a field is
    unavailable for a format (PDFs have no notion of a 'List Bullet' style) the
    parser derives the closest equivalent instead of dropping it.
    """
    text: str
    is_bullet: bool = False
    is_bold: bool = False
    is_heading: bool = False     # an explicit Heading style (DOCX only)
    font_size: float = 0.0
    page: int = 1


@dataclass
class ParsedDoc:
    lines: list[Line] = field(default_factory=list)
    source_type: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(l.text) for l in self.lines)


def _strip_bullet(text: str) -> tuple[str, bool]:
    """Removes a leading bullet glyph. Returns (clean_text, was_bulleted).

    The glyph is stripped so it never reaches the embedding -- '- Built 12 APIs'
    and 'Built 12 APIs' should produce the same vector. The FACT that it was a
    bullet is kept in the Line record, where it does real work.
    """
    stripped = text.lstrip()
    for ch in BULLET_CHARS:
        if stripped.startswith(ch):
            rest = stripped[len(ch):].lstrip()
            # A hyphen only counts as a bullet if something follows it, so we
            # don't mistake a date range like "- 2019" for a list item.
            if rest:
                return rest, True
    return stripped, False


# ============================================================
# PARSER: PDF
# ============================================================

def parse_pdf(path: Path) -> ParsedDoc:
    """Rebuilds visual lines from positioned words, keeping font and weight.

    pdfplumber's extract_text() would give us a string and throw the formatting
    away. extract_words(extra_attrs=...) keeps fontname and size per word, so we
    group words into lines by their vertical position and derive:
      is_bold   -- from the font name (e.g. 'AAAAAA+DejaVuSans-Bold')
      font_size -- the largest size on the line, used to spot headings
    """
    doc = ParsedDoc(source_type="pdf")

    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(
                extra_attrs=["fontname", "size"], use_text_flow=False
            )
            if not words:
                continue

            # Group words into lines. Words on the same visual line share a very
            # similar 'top', but never exactly -- superscripts and mixed font
            # sizes shift it by a point or two, so we bucket with a tolerance.
            rows: list[list[dict]] = []
            for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
                if rows and abs(w["top"] - rows[-1][0]["top"]) <= 2.5:
                    rows[-1].append(w)
                else:
                    rows.append([w])

            for row in rows:
                row.sort(key=lambda w: w["x0"])
                # pdfplumber returns positioned WORDS, and punctuation often
                # arrives as its own word, so a naive join gives "Analyst , Ola".
                raw = " ".join(w["text"] for w in row).strip()
                raw = re.sub(r"\s+([,;.:!?%)])", r"\1", raw)
                raw = re.sub(r"\(\s+", "(", raw)
                if not raw:
                    continue
                text, is_bullet = _strip_bullet(raw)
                if not text:
                    continue
                doc.lines.append(Line(
                    text=text,
                    is_bullet=is_bullet,
                    # "Bold" appears in the PostScript font name of the bold face.
                    is_bold=any("bold" in w["fontname"].lower() for w in row),
                    font_size=max(w["size"] for w in row),
                    page=page_no,
                ))

    doc.lines = _merge_wrapped_lines(doc.lines)

    if doc.char_count < MIN_DOC_CHARS:
        doc.warnings.append(
            "very little text extracted - likely a scanned/image-only PDF "
            "(production fix: OCR fallback)"
        )
    return doc


def _merge_wrapped_lines(lines: list[Line]) -> list[Line]:
    """Rejoins a sentence that the PDF wrapped across two visual lines.

    This is the fundamental difference between the two formats. DOCX stores
    LOGICAL paragraphs, so a long bullet is one line no matter how it renders.
    A PDF stores only VISUAL lines, so the same bullet arrives as two, and the
    second one has no bullet glyph in front of it.

    That matters because the entry state machine treats 'a plain line after
    bullets' as the start of the next job. Left unmerged, one wrapped bullet
    silently split a single job into two chunks -- which is how a 3-job resume
    came out as 8 entries.

    A continuation is detected as: the previous line does not end in sentence-
    final punctuation, AND this line does not begin with a capital. That catches
    both '...for the payments' + 'platform.' and '...latency from' + '320ms'.
    """
    merged: list[Line] = []
    for line in lines:
        if merged:
            prev = merged[-1]
            continues = (
                prev.page == line.page
                and not line.is_bullet
                and not prev.text.endswith((".", "!", "?", ":", ";"))
                and not line.text[:1].isupper()
                # A wrapped line is always the SAME size as the line it came
                # from. Without this guard, a section heading followed by a
                # date ("PROFESSIONAL EXPERIENCE" then "2021 to 2023") merges
                # into one line, the heading stops matching the section map,
                # and the entire EXPERIENCE section disappears.
                and abs(prev.font_size - line.font_size) < 0.6
                and _normalise_heading(prev.text) not in CANONICAL_SECTIONS
            )
            if continues:
                prev.text = f"{prev.text} {line.text}"
                continue
        merged.append(line)
    return merged


# ============================================================
# PARSER: DOCX
# ============================================================

def _iter_docx_blocks(document: Document):
    """Yields paragraphs and tables in true document order.

    document.paragraphs SKIPS anything inside a table, and plenty of real
    resumes lay out their whole contact block or skills grid as a table. Walking
    the body XML instead means we never silently lose those.
    """
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def parse_docx(path: Path) -> ParsedDoc:
    """Reads paragraph style, run weight and font size straight from the file.

    DOCX declares its structure explicitly, so this parser mostly just has to
    not throw it away:
      is_bullet  -- the paragraph style is a List/Bullet style. NOTE the bullet
                    glyph is numbering, not text, so p.text has no '-' in it.
                    A text-based bullet check would return False for every
                    bulleted line in the document.
      is_heading -- a real 'Heading N' style.
      is_bold    -- the FIRST run's weight, because resume lines are typically
                    'BoldTitle, plain company' and the title is what identifies
                    the line as an entry header.
    """
    doc = ParsedDoc(source_type="docx")
    document = Document(path)

    def add_paragraph(p: Paragraph) -> None:
        raw = p.text.strip()
        if not raw:
            return
        style = (p.style.name or "").lower()
        runs = [r for r in p.runs if r.text.strip()]
        size = 0.0
        for r in runs:
            if r.font.size is not None:
                size = max(size, r.font.size.pt)

        # A single paragraph can hold a soft line break (python-docx turns <w:br/>
        # back into '\n'), so split it into separate visual lines.
        for part in raw.split("\n"):
            part = part.strip()
            if not part:
                continue
            text, glyph_bullet = _strip_bullet(part)
            if not text:
                continue
            doc.lines.append(Line(
                text=text,
                is_bullet=("list" in style and "bullet" in style)
                          or "list paragraph" in style
                          or glyph_bullet,
                is_bold=bool(runs and runs[0].bold),
                is_heading=style.startswith("heading"),
                font_size=size,
            ))

    for block in _iter_docx_blocks(document):
        if isinstance(block, Paragraph):
            add_paragraph(block)
        else:
            for row in block.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        add_paragraph(p)

    if doc.char_count < MIN_DOC_CHARS:
        doc.warnings.append("very little text extracted - possibly an empty or broken file")
    return doc


def parse_document(path: Path) -> ParsedDoc:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return parse_pdf(path)
    if ext == ".docx":
        return parse_docx(path)
    raise ValueError(f"unsupported file type: {ext}")


# ============================================================
# TIER 1: SECTION SPLITTING
# ============================================================

def _normalise_heading(text: str) -> str:
    clean = re.sub(r"^#+\s*", "", text.strip())
    clean = re.sub(r"[:\-_\u2013\u2014]+$", "", clean).strip()
    return re.sub(r"\s+", " ", clean).upper()


def split_into_sections(
    doc: ParsedDoc,
) -> tuple[list[tuple[str, list[Line]]], list[str]]:
    """Groups lines under section headings.

    Two ways a line is recognised as a heading:
      1. Its text matches CANONICAL_SECTIONS. Reliable, and format-independent.
      2. It LOOKS like a heading structurally -- bold or a Heading style, short,
         and visually larger than the body text.

    Case 2 is what catches sections the map has never seen ('LANGUAGES',
    'VOLUNTEER EXPERIENCE'). The previous version had no such rule, so an
    unrecognised heading and all its content were silently appended to whatever
    section came before -- a HOBBIES block quietly became part of EDUCATION.
    Those now become an explicit OTHER section and get reported, so you know
    which aliases are worth adding to the map.
    """
    body_sizes = [l.font_size for l in doc.lines if l.font_size and not l.is_bold]
    body_size = statistics.median(body_sizes) if body_sizes else 0.0

    sections: list[tuple[str, list[Line]]] = [("HEADER", [])]
    unknown_headings: list[str] = []
    seen_first_section = False

    def looks_structural(line: Line) -> bool:
        if line.is_bullet:
            return False
        if len(line.text.split()) > 5:
            return False
        # Before the first real section we are still inside the name/contact
        # block, where the candidate's name is bold and large. Treating that as
        # a heading would throw the name away, so we require a known heading to
        # have appeared first.
        if not seen_first_section:
            return False
        if line.is_heading:
            return True
        # Must be visually LARGER than body text, not merely bold and short.
        # Bold-and-uppercase alone is not enough: degree names like "BCA" and
        # "MCA" are bold, one word and uppercase, and were being promoted to
        # section headings -- which silently tore the EDUCATION section apart.
        bigger = bool(body_size) and line.font_size >= body_size + 1.0
        return line.is_bold and bigger

    for line in doc.lines:
        canonical = CANONICAL_SECTIONS.get(_normalise_heading(line.text))

        if canonical:
            seen_first_section = True
            sections.append((canonical, []))
            continue

        if looks_structural(line):
            unknown_headings.append(line.text)
            sections.append(("OTHER", []))
            continue

        sections[-1][1].append(line)

    return [(name, lines) for name, lines in sections if lines], unknown_headings


# ============================================================
# TIER 2: ENTRY SPLITTING (the state machine)
# ============================================================

def split_into_entries(lines: list[Line]) -> list[list[Line]]:
    """Splits one section into individual entries: one job, degree or project.

    An entry has a shape: a HEADER BLOCK of one or more non-bullet lines (title,
    company, dates, institution), followed by a BODY of bullets. So a new entry
    begins whenever we see a header-looking line after we have already left the
    header block behind. Two independent signals say we have left it:

      RULE 1 -- a non-bullet line arriving after bullets have started. Bullets
                belong to a job; the next plain line must be the next job.
      RULE 2 -- a second bold line in the same entry. Bold marks a title, and
                one entry only has one title. This is the rule that finally
                splits PROJECTS, which contain no dates for a regex to find.

    Rule 2 is deliberately conditioned on having ALREADY seen a bold line, so
    the very common 'bold title' + 'plain dates' pair, and the reverse 'plain
    dates' + 'bold title' pair, both survive as one entry.
    """
    entries: list[list[Line]] = []
    current: list[Line] = []
    seen_bullet = False
    seen_bold = False

    def flush() -> None:
        nonlocal current, seen_bullet, seen_bold
        if current:
            entries.append(current)
        current, seen_bullet, seen_bold = [], False, False

    for line in lines:
        if line.is_bullet:
            current.append(line)
            seen_bullet = True
            continue

        starts_new_entry = bool(current) and (
            seen_bullet                      # RULE 1
            or (line.is_bold and seen_bold)  # RULE 2
        )
        if starts_new_entry:
            flush()

        current.append(line)
        if line.is_bold:
            seen_bold = True

    flush()
    return entries


# ============================================================
# SIZE GUARD
# ============================================================

def enforce_max_chars(lines: list[Line], max_chars: int) -> list[list[Line]]:
    """Splits an oversized entry on line boundaries, never mid-sentence.

    The entry's header lines are REPEATED at the top of every continuation
    chunk. Without that, part 2 of a long job is a bare list of bullets with no
    company attached -- retrievable, but useless as evidence and impossible for
    the LLM to attribute.

    Note there is no sliding overlap here, unlike generic chunkers. Overlap
    exists to stop a fixed-size splitter cutting through a thought. Our
    boundaries are already semantic, so overlap would only duplicate content
    and let one job match a query twice.
    """
    text_len = sum(len(l.text) + 1 for l in lines)
    if text_len <= max_chars:
        return [lines]

    header = [l for l in lines[:3] if not l.is_bullet]
    header_len = sum(len(l.text) + 1 for l in header)

    parts: list[list[Line]] = []
    buffer: list[Line] = []
    size = 0

    for line in lines:
        line_len = len(line.text) + 1
        if buffer and size + line_len > max_chars:
            parts.append(buffer)
            buffer = list(header)
            size = header_len
        buffer.append(line)
        size += line_len

    if buffer:
        parts.append(buffer)
    return parts


# ============================================================
# CHUNK BUILDING
# ============================================================

def _lines_to_text(lines: list[Line]) -> str:
    out = []
    for l in lines:
        out.append(f"- {l.text}" if l.is_bullet else l.text)
    return "\n".join(out)


def _guess_candidate_name(sections: list[tuple[str, list[Line]]]) -> str:
    """The first line of the HEADER block is the candidate's name on almost
    every resume. Only a heuristic -- the authoritative name comes from the LLM
    extraction stage later -- but good enough to label chunks with now."""
    for name, lines in sections:
        if name == "HEADER" and lines:
            return lines[0].text
    return ""


def chunk_resume(path: Path) -> tuple[list[dict], dict]:
    parsed = parse_document(path)
    sections, unknown_headings = split_into_sections(parsed)
    candidate_name = _guess_candidate_name(sections)

    chunks: list[dict] = []
    counter = 0

    for section_name, lines in sections:
        if section_name in MULTI_ENTRY_SECTIONS:
            groups = split_into_entries(lines)
            max_chars = MAX_CHARS_ENTRY
        else:
            groups = [lines]
            max_chars = MAX_CHARS_FLAT

        for item_index, group in enumerate(groups, start=1):
            for part_index, part in enumerate(enforce_max_chars(group, max_chars), start=1):
                text = _lines_to_text(part)
                if not text.strip():
                    continue
                counter += 1

                # CONTEXTUAL PREFIX: what we EMBED is not what we DISPLAY.
                # "Built 12 REST endpoints" embedded alone is nearly meaningless
                # and cannot be attributed to anyone. Prefixing the candidate and
                # section gives the vector something to anchor on -- the same
                # idea as Anthropic's "contextual retrieval". The raw text is
                # kept separately so the recruiter still sees clean evidence.
                prefix = f"[{candidate_name} | {section_name}]" if candidate_name else f"[{section_name}]"

                chunks.append({
                    "chunk_id": f"{path.stem}_{section_name.lower()}_{item_index}_{part_index}",
                    "text": text,
                    "embedding_text": f"{prefix}\n{text}",
                    "metadata": {
                        "resume_id": path.stem,
                        "source_file": path.name,
                        "source_type": parsed.source_type,
                        "candidate_name": candidate_name,
                        "section": section_name,
                        "item_index": item_index,
                        "part_index": part_index,
                        "chunk_index": counter,
                        "char_count": len(text),
                        "line_count": len(part),
                    },
                })

    report = {
        "resume_id": path.stem,
        "source_type": parsed.source_type,
        "n_chunks": len(chunks),
        "n_lines": len(parsed.lines),
        "doc_chars": parsed.char_count,
        "unknown_headings": unknown_headings,
        "warnings": parsed.warnings,
        "sections_found": sorted({c["metadata"]["section"] for c in chunks}),
    }
    return chunks, report


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    if not RESUME_DIR.exists():
        print(f"'{RESUME_DIR}' does not exist. Run generate_resume.py first.")
        return

    files = sorted(list(RESUME_DIR.glob("*.pdf")) + list(RESUME_DIR.glob("*.docx")))
    if not files:
        print(f"No PDF or DOCX files in '{RESUME_DIR}'.")
        return

    all_chunks: list[dict] = []
    reports: list[dict] = []
    failures: list[tuple[str, str]] = []

    for path in files:
        # One bad file must never kill a 100-resume batch.
        try:
            chunks, report = chunk_resume(path)
        except Exception as exc:
            failures.append((path.name, f"{type(exc).__name__}: {exc}"))
            continue
        all_chunks.extend(chunks)
        reports.append(report)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)

    sizes = [c["metadata"]["char_count"] for c in all_chunks] or [0]
    unknown = sorted({h for r in reports for h in r["unknown_headings"]})
    warned = [r for r in reports if r["warnings"]]

    print(f"parsed        : {len(reports)}/{len(files)} files")
    print(f"chunks        : {len(all_chunks)}")
    print(f"chunk chars   : min {min(sizes)}, median {int(statistics.median(sizes))}, max {max(sizes)}")
    if unknown:
        print(f"unknown headings (candidates for CANONICAL_SECTIONS): {', '.join(unknown)}")
    for r in warned:
        print(f"  WARNING {r['resume_id']}: {'; '.join(r['warnings'])}")
    for name, err in failures:
        print(f"  FAILED  {name}: {err}")
    print(f"\nwritten to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()