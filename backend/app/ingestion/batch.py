"""
Batch ingestion: an uploaded ZIP becomes chunks, safely.

A ZIP FROM A USER IS UNTRUSTED INPUT
-------------------------------------
Two attacks matter, and both are trivial to launch:

  ZIP SLIP     an entry named "../../../../etc/passwd" or an absolute path
               escapes the extraction directory and overwrites files elsewhere
               on the server. Python's zipfile.extractall() sanitises some of
               this, but the check belongs in your code where you can see it.

  ZIP BOMB     a 1MB archive that expands to 40GB and fills the disk. The
               defence is a cap on TOTAL uncompressed size and on the
               compression RATIO of any single entry, both checked from the
               header BEFORE extracting anything.

Neither is exotic. A hiring tool accepts files from strangers by design, which
makes it exactly the kind of endpoint people probe.

ONE BAD FILE MUST NOT KILL A BATCH OF 100
------------------------------------------
Real uploads contain: scanned image-only PDFs with no text layer, old binary
.doc files, password-protected PDFs, __MACOSX junk, .DS_Store, nested folders,
duplicates, and at least one corrupt file. Every file gets a STATUS and the
batch continues.

The status that matters most is `empty_text`. A scanned PDF parses
"successfully" and yields 40 characters. Indexed silently, that candidate ranks
last on every requirement forever and nobody ever finds out why. Flagging it
turns an invisible failure into a line in a report.

WHY EVERY BATCH HAS A job_id
-----------------------------
The second recruiter to use the system must not see the first one's candidates,
and the first one's candidates must not be deleted to make room. So every batch
gets an id, and it is attached to every chunk and every database row.

That id is also the unit of DELETION. Resumes are personal data: India's DPDP
Act and the GDPR both require keeping it only as long as the stated purpose
needs. A retention policy is "delete everything with this job_id after N days",
which only works if the id reaches all three stores -- SQL rows, vectors, and
files. Deleting from one and not the others leaves orphan vectors that keep
surfacing as evidence for a person the system claims not to have.

PRODUCTION vs LEARNING SHORTCUT
------------------------------
Extraction to local disk, processed in-process. Production would stream to
object storage, scan for malware, process in a worker queue, and enforce
per-tenant quotas. The status model below is the part worth keeping as-is.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional

from app.chunking.chunker import chunk_resume

BASE = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BASE / "data" / "uploads"

# Caps checked from the ZIP header before anything is written to disk.
MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024     # 500 MB across the whole archive
MAX_FILE_UNCOMPRESSED = 25 * 1024 * 1024       # 25 MB for any single resume
MAX_COMPRESSION_RATIO = 200                     # a 200x entry is a bomb, not a resume
MAX_FILES = 1000

SUPPORTED = {".pdf", ".docx"}

# Below this, a "successful" parse is almost certainly a scanned image.
MIN_TEXT_CHARS = 200

# Junk that macOS and Windows add to archives.
JUNK_PREFIXES = ("__MACOSX/", ".")
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


class FileStatus(str, Enum):
    PARSED = "parsed"
    EMPTY_TEXT = "empty_text"           # parsed, but almost no text -- likely scanned
    UNSUPPORTED = "unsupported_format"
    DUPLICATE = "duplicate"
    CORRUPT = "corrupt"
    ENCRYPTED = "encrypted"
    TOO_LARGE = "too_large"


@dataclass
class FileResult:
    name: str
    status: FileStatus
    resume_id: Optional[str] = None
    n_chunks: int = 0
    n_chars: int = 0
    detail: str = ""
    duplicate_of: Optional[str] = None


@dataclass
class BatchResult:
    job_id: str
    files: list[FileResult] = field(default_factory=list)
    chunks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def parsed(self) -> list[FileResult]:
        return [f for f in self.files if f.status == FileStatus.PARSED]

    @property
    def failed(self) -> list[FileResult]:
        return [f for f in self.files if f.status != FileStatus.PARSED]

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for f in self.files:
            counts[f.status.value] = counts.get(f.status.value, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        return (f"job {self.job_id}: {len(self.files)} files "
                f"({', '.join(parts)}) -> {len(self.chunks)} chunks")


# ============================================================
# SAFE EXTRACTION
# ============================================================

def _is_junk(name: str) -> bool:
    if name.endswith("/"):
        return True
    base = Path(name).name
    return (name.startswith(JUNK_PREFIXES[0])
            or base in JUNK_NAMES
            or base.startswith("."))


def inspect_archive(zip_path: Path) -> list[str]:
    """Validates the archive from its headers. Raises before extracting anything.

    Everything here is checked WITHOUT decompressing, using the sizes the ZIP
    central directory declares. That is the whole point: a zip bomb is only
    dangerous once you start writing it to disk.
    """
    problems = []
    with zipfile.ZipFile(zip_path) as archive:
        entries = [e for e in archive.infolist() if not _is_junk(e.filename)]

        if len(entries) > MAX_FILES:
            raise ValueError(f"archive contains {len(entries)} files, limit {MAX_FILES}")

        total = sum(e.file_size for e in entries)
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise ValueError(
                f"archive expands to {total / 1e6:.0f}MB, limit "
                f"{MAX_TOTAL_UNCOMPRESSED / 1e6:.0f}MB"
            )

        for entry in entries:
            # ZIP entry names are always forward-slash strings, independent of
            # the host OS. Checking them with pathlib made this platform-
            # dependent: Path("/safe")/"cv.pdf" resolves to C:\safe\cv.pdf on
            # Windows, so every legitimate file was rejected. Inspect the raw
            # name instead.
            name = entry.filename.replace("\\", "/")
            if (name.startswith("/")
                    or name.startswith("../")
                    or "/../" in name
                    or (len(name) > 1 and name[1] == ":")):     # C:\ drive paths
                raise ValueError(f"unsafe path in archive: {entry.filename}")

            if entry.compress_size > 0:
                ratio = entry.file_size / entry.compress_size
                if ratio > MAX_COMPRESSION_RATIO and entry.file_size > 1_000_000:
                    raise ValueError(
                        f"{entry.filename} expands {ratio:.0f}x - refusing as a "
                        f"possible zip bomb"
                    )
    return problems


def extract_archive(zip_path: Path, target: Path) -> list[Path]:
    """Extracts supported files into a flat directory.

    Flattened on purpose: candidates get sent as "2024/backend/cv.pdf" and
    nested structure carries no meaning here. Name collisions are resolved by
    prefixing, so two files called cv.pdf in different folders both survive.
    """
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    with zipfile.ZipFile(zip_path) as archive:
        for entry in archive.infolist():
            if _is_junk(entry.filename):
                continue
            suffix = Path(entry.filename).suffix.lower()
            if suffix not in SUPPORTED:
                continue
            if entry.file_size > MAX_FILE_UNCOMPRESSED:
                continue

            safe_name = Path(entry.filename).name
            out = target / safe_name
            if out.exists():
                stem = Path(entry.filename).parent.name or "dup"
                out = target / f"{stem}_{safe_name}"

            with archive.open(entry) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            written.append(out)

    return sorted(written)


# ============================================================
# PROCESSING
# ============================================================

def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()[:16]


def classify_error(exc: Exception) -> tuple[FileStatus, str]:
    text = str(exc).lower()
    if "password" in text or "encrypt" in text:
        return FileStatus.ENCRYPTED, "password protected"
    return FileStatus.CORRUPT, f"{type(exc).__name__}: {exc}"[:120]


def process_files(files: list[Path], job_id: str) -> BatchResult:
    """Chunks every file, recording a status for each.

    Deduplication is by CONTENT hash, not filename. The same person applying
    twice under different filenames must not appear twice in a shortlist -- a
    duplicate candidate is the kind of bug a recruiter notices before you do.
    """
    result = BatchResult(job_id=job_id)
    seen: dict[str, str] = {}

    for path in files:
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED:
            result.files.append(FileResult(path.name, FileStatus.UNSUPPORTED,
                                           detail=f"{suffix} not supported"))
            continue

        digest = file_hash(path)
        if digest in seen:
            result.files.append(FileResult(path.name, FileStatus.DUPLICATE,
                                           duplicate_of=seen[digest]))
            continue

        try:
            chunks, report = chunk_resume(path)
        except Exception as exc:
            status, detail = classify_error(exc)
            result.files.append(FileResult(path.name, status, detail=detail))
            continue

        chars = report["doc_chars"]
        if chars < MIN_TEXT_CHARS:
            # Parsed without error but produced almost nothing. Recorded and
            # NOT indexed: an invisible near-empty candidate silently ranks last
            # forever. Production answer is an OCR fallback.
            result.files.append(FileResult(
                path.name, FileStatus.EMPTY_TEXT, resume_id=report["resume_id"],
                n_chars=chars,
                detail="likely a scanned image with no text layer (needs OCR)",
            ))
            continue

        # job_id on every chunk, so retrieval and deletion can both be scoped
        # to one batch.
        for chunk in chunks:
            chunk["metadata"]["job_id"] = job_id
        result.chunks.extend(chunks)

        seen[digest] = path.name
        result.files.append(FileResult(
            path.name, FileStatus.PARSED, resume_id=report["resume_id"],
            n_chunks=len(chunks), n_chars=chars,
        ))

        if report["unknown_headings"]:
            result.warnings.append(
                f"{path.name}: unrecognised headings "
                f"{', '.join(report['unknown_headings'][:3])}"
            )

    return result


def ingest_zip(zip_path: Path, job_id: Optional[str] = None) -> BatchResult:
    """Full path from an uploaded ZIP to chunks, with a status per file."""
    job_id = job_id or f"job_{datetime.now():%Y%m%d_%H%M%S}"
    inspect_archive(zip_path)

    workdir = UPLOAD_DIR / job_id
    files = extract_archive(zip_path, workdir)
    if not files:
        raise ValueError("archive contained no .pdf or .docx files")

    return process_files(files, job_id)


def ingest_directory(directory: Path, job_id: Optional[str] = None) -> BatchResult:
    """Same processing without a ZIP. Useful for local testing and for the
    synthetic corpus, which lives in a folder."""
    job_id = job_id or f"job_{datetime.now():%Y%m%d_%H%M%S}"
    files = sorted(
        p for p in directory.iterdir() if p.suffix.lower() in SUPPORTED
    )
    if not files:
        raise ValueError(f"no .pdf or .docx files in {directory}")
    return process_files(files, job_id)


def format_report(result: BatchResult) -> str:
    lines = [result.summary(), ""]

    if result.failed:
        lines.append("files needing attention:")
        for f in result.failed:
            extra = f" ({f.detail})" if f.detail else ""
            if f.duplicate_of:
                extra = f" (same content as {f.duplicate_of})"
            lines.append(f"   {f.status.value:18} {f.name[:38]:38}{extra}")
        lines.append("")

    if result.warnings:
        lines.append("warnings:")
        for w in result.warnings[:8]:
            lines.append(f"   {w}")
        lines.append("")

    parsed = result.parsed
    if parsed:
        chars = [f.n_chars for f in parsed]
        lines.append(f"parsed {len(parsed)} resumes, "
                     f"{sum(f.n_chunks for f in parsed)} chunks, "
                     f"text {min(chars)}-{max(chars)} chars")
    return "\n".join(lines)