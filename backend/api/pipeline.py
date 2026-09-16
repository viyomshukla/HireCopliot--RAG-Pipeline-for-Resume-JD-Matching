"""
The background pipeline: an uploaded ZIP becomes a searchable, rankable batch.

WHY THIS CANNOT HAPPEN INSIDE THE REQUEST
------------------------------------------
Processing 100 resumes is: parse and chunk (seconds), LLM extraction (minutes,
rate-limited), embed and index (seconds). An HTTP request that takes four
minutes will be killed by a proxy, a load balancer, or the browser long before
it finishes, and the user has no idea whether anything happened.

So upload returns a job_id immediately and the work runs in the background. The
client polls for status. This is the standard shape for any long-running job
over HTTP, and the reason the endpoint returns 202 rather than 200.

THE JOB REGISTRY IS IN MEMORY, AND THAT IS A REAL LIMITATION
-------------------------------------------------------------
Restarting the server loses every job's status. Two server processes do not see
each other's jobs, so this cannot be scaled horizontally.

Both are acceptable while learning and neither is acceptable in production. The
upgrade is Redis for state plus Celery or RQ for the worker, which also gives
retries, timeouts, and a queue that survives a deploy. The code below is
deliberately structured so that swap touches this file only: everything else
just calls `registry.get(job_id)`.

PARTIAL FAILURE IS RECORDED, NOT RAISED
----------------------------------------
If extraction fails for 8 of 100 resumes, the job still succeeds with 92. A
pipeline that aborts the batch because one PDF was a scan would be unusable on
real data. Every stage records what it could not do and carries on.
"""

from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from api.schemas import FileReport, JobStatus

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"


class JobRegistry:
    """Thread-safe store of job status.

    A lock rather than a plain dict because FastAPI's background tasks run in a
    worker thread while requests are served on another. Without it, a status
    poll can read a half-updated record.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobStatus] = {}
        self._lock = threading.Lock()

    def create(self, job_id: str) -> JobStatus:
        status = JobStatus(job_id=job_id, state="queued", created_at=datetime.now())
        with self._lock:
            self._jobs[job_id] = status
        return status

    def get(self, job_id: str) -> Optional[JobStatus]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[JobStatus]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            status = self._jobs.get(job_id)
            if status is None:
                return
            for key, value in fields.items():
                setattr(status, key, value)


registry = JobRegistry()


# ============================================================
# THE PIPELINE
# ============================================================

def chunks_path(job_id: str) -> Path:
    return DATA / f"chunks_{job_id}.json"


def run_pipeline(job_id: str, source: Path, is_zip: bool) -> None:
    """Ingest -> extract -> load database -> index. Runs in a background thread.

    Stage boundaries are also the checkpoints: each writes its output to disk
    before the next starts, so a failure in extraction leaves usable chunks
    behind rather than nothing.
    """
    try:
        _ingest(job_id, source, is_zip)
        _extract(job_id)
        _index(job_id)
        registry.update(
            job_id, state="ready", progress=1.0,
            stage_message="ready to rank", finished_at=datetime.now(),
        )
    except Exception as exc:
        # The traceback goes to the log; the client gets one line. Exposing a
        # stack trace over HTTP leaks file paths and library versions.
        traceback.print_exc()
        registry.update(
            job_id, state="failed", error=f"{type(exc).__name__}: {exc}",
            finished_at=datetime.now(),
        )


def _ingest(job_id: str, source: Path, is_zip: bool) -> None:
    from app.ingestion.batch import ingest_directory, ingest_zip

    registry.update(job_id, state="ingesting", progress=0.05,
                    stage_message="reading files")

    result = ingest_zip(source, job_id) if is_zip else ingest_directory(source, job_id)

    chunks_path(job_id).write_text(
        json.dumps(result.chunks, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    registry.update(
        job_id,
        n_files=len(result.files),
        n_parsed=len(result.parsed),
        n_chunks=len(result.chunks),
        progress=0.2,
        stage_message=f"parsed {len(result.parsed)} of {len(result.files)} files",
        files=[
            FileReport(name=f.name, status=f.status.value, resume_id=f.resume_id,
                       n_chunks=f.n_chunks, detail=f.detail or (
                           f"same content as {f.duplicate_of}" if f.duplicate_of else ""))
            for f in result.files
        ],
    )


def _extract(job_id: str) -> None:
    from app.extraction.extractor import extract_batch, group_chunks_by_resume

    registry.update(job_id, state="extracting", progress=0.25,
                    stage_message="extracting structured data (this is the slow part)")

    chunks = json.loads(chunks_path(job_id).read_text(encoding="utf-8"))
    grouped = group_chunks_by_resume(chunks)

    records, failures = extract_batch(grouped, workers=2, progress=False)

    (DATA / f"extractions_{job_id}.json").write_text(
        json.dumps([r.model_dump(mode="json") for r in records], indent=2,
                   ensure_ascii=False),
        encoding="utf-8",
    )

    _load_database(job_id, records)

    registry.update(
        job_id, n_candidates=len(records), progress=0.75,
        stage_message=(f"extracted {len(records)} candidates"
                       + (f", {len(failures)} failed" if failures else "")),
    )


def _load_database(job_id: str, records) -> None:
    from app.db.session import get_session, init_db
    from scripts.load_database import load_record

    init_db()
    with get_session() as session:
        for record in records:
            data = record.model_dump(mode="json")
            candidate, _n, _u = load_record(session, data)
            # Stamp the batch so one recruiter's candidates never appear in
            # another's ranking, and so a retention policy can delete by batch.
            if hasattr(candidate, "job_id"):
                candidate.job_id = job_id


def _index(job_id: str) -> None:
    from app.retrieval.vector_store import VectorStore

    registry.update(job_id, state="indexing", progress=0.8,
                    stage_message="building the vector index")

    chunks = json.loads(chunks_path(job_id).read_text(encoding="utf-8"))
    VectorStore().index_chunks(chunks)

    # The retriever caches a BM25 index built from whichever chunk files existed
    # when it was first constructed. A batch uploaded afterwards is invisible to
    # it until the cache is dropped.
    import api.main
    api.main._retriever = None