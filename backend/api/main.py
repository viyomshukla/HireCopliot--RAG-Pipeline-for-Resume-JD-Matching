##uvicorn api.main:app --reload --port 8000
"""
The HTTP API.

    uvicorn api.main:app --reload --port 8000
    open http://localhost:8000/docs

ENDPOINTS
---------
    POST /api/upload            a ZIP of resumes -> job_id, processed in background
    GET  /api/jobs              every batch
    GET  /api/jobs/{job_id}     progress and per-file status
    POST /api/rank              a JD -> ranked shortlist with evidence
    POST /api/audit             disparate-impact audit of a shortlist
    DELETE /api/jobs/{job_id}   delete a batch from every store

WHY UPLOAD RETURNS 202 AND NOT THE RESULT
------------------------------------------
Processing 100 resumes takes minutes, mostly waiting on a rate-limited LLM. A
request that long gets killed by a proxy or the browser. So the server accepts
the file, returns a job_id, and the client polls. 202 Accepted is the status
code that says exactly that: taken, not finished.

WHY THE RANKING RESPONSE INCLUDES THE PARSED REQUIREMENTS
----------------------------------------------------------
The rubric the system inferred from the JD is returned alongside the results,
so a recruiter can see what it decided they were asking for. A ranking whose
criteria are invisible cannot be challenged, and for an employment tool being
challengeable is the entire point.

The same reasoning drives returning excluded candidates with their reasons
rather than dropping them.
"""

from __future__ import annotations

import shutil
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi.responses import RedirectResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(BASE))
# The API loads .env itself. Scripts each call load_dotenv(), but uvicorn starts
# the app directly, so without this the server runs with no API keys and the
# first LLM call fails three layers deep with a confusing error.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
except ImportError:
    pass

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile


from api.pipeline import chunks_path, registry, run_pipeline
from api.schemas import (
    AttributeAuditResponse, AuditResponse, DuplicateRef, EvidenceItem, GroupStat,
    JobStatus, ParsedRequirement, RankRequest, RankResponse, RankedCandidate,
    UploadResponse,
)

DATA = BASE / "data"
UPLOAD_DIR = DATA / "uploads"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Brings an existing database up to the current schema before the first
    # request. The ranker selects columns added after release (duplicate_of);
    # without this, ranking fails on an old database until someone happens to
    # upload a batch, which is the only other path that calls init_db().
    from app.db.session import init_db
    init_db()
    yield


app = FastAPI(
    title="HireMind API",
    description="Ranks candidates against a job description with cited evidence.",
    version="0.1.0",
    lifespan=lifespan,
)

# The Next.js dev server runs on a different port, so the browser treats it as a
# different origin. Locked to localhost: a wildcard here would let any website a
# user visits call this API with their session.


# Retrieval components are expensive to construct -- a local embedding model and
# a cross-encoder take seconds to load and hold memory. Built once, lazily, and
# reused across requests.
_retriever = None


def get_retriever():
    global _retriever
    if _retriever is None:
        import json
        from app.retrieval.embeddings import get_embedder
        from app.retrieval.fusion import HybridRetriever
        from app.retrieval.keyword import BM25Index
        from app.retrieval.reranker import RerankedRetriever, get_reranker
        from app.retrieval.vector_store import VectorStore

        # BM25 has no persistent index; it is rebuilt from whatever chunk files
        # exist. Cheap at this scale, and the production answer is a real
        # inverted index rather than caching this one.
        chunks = []
        for path in sorted(DATA.glob("chunks*.json")):
            chunks.extend(json.loads(path.read_text(encoding="utf-8")))
        if not chunks:
            raise HTTPException(503, "no chunks indexed yet - upload a batch first")

        _retriever = RerankedRetriever(
            HybridRetriever(VectorStore(), BM25Index(chunks)),
            get_reranker(), retrieve_depth=10,
        )
    return _retriever


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "jobs": len(registry.list())}


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload", response_model=UploadResponse, status_code=202)
async def upload(
    background: BackgroundTasks,
    file: UploadFile = File(..., description="A .zip of .pdf and .docx resumes"),
    # Optional, and generated per request below. A default written as an
    # f-string in the signature is evaluated ONCE, at import: every upload in a
    # server session got the same id, and the second one failed with 409.
    job_id: Optional[str] = Form(None),
) -> UploadResponse:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "upload a .zip file")

    job_id = job_id or f"job_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    if registry.get(job_id):
        raise HTTPException(409, f"job '{job_id}' already exists")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_DIR / f"{job_id}.zip"

    # Streamed to disk in blocks rather than read into memory. A 200MB upload
    # read with .read() is 200MB of RAM per concurrent request.
    size = 0
    with open(target, "wb") as out:
        while block := await file.read(1024 * 1024):
            size += len(block)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, f"file exceeds {MAX_UPLOAD_BYTES // 1_000_000}MB")
            out.write(block)

    registry.create(job_id)
    background.add_task(run_pipeline, job_id, target, True)

    return UploadResponse(
        job_id=job_id, state="queued",
        message="accepted - poll /api/jobs/{job_id} for progress",
    )


@app.get("/api/jobs", response_model=list[JobStatus])
def list_jobs() -> list[JobStatus]:
    return registry.list()


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def job_status(job_id: str) -> JobStatus:
    status = registry.get(job_id)
    if status is None:
        raise HTTPException(404, f"unknown job '{job_id}'")
    return status


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    """Deletes a batch from every store: SQL, vectors, files, and the registry.

    Deleting SQL rows but leaving vectors behind produces orphan chunks that
    keep surfacing as evidence for a candidate the system claims not to have --
    which is both a bug and, for personal data, a compliance failure.

    THE FOUR THINGS THE PREVIOUS VERSION GOT WRONG
    ----------------------------------------------
    1. It never removed the job from the registry, so the batch reappeared in
       /api/jobs straight after a "successful" delete.
    2. It deleted candidates on a raw sqlite3 connection without
       `PRAGMA foreign_keys = ON`, so every ON DELETE CASCADE was ignored and
       skills, experience and education rows were orphaned -- to be inherited
       by the next candidate that reused the id.
    3. It left the cached retriever alone, so BM25 kept returning chunks from
       the deleted batch as evidence.
    4. It would delete a batch mid-pipeline, which the background task then
       half-recreated.

    Every step is idempotent, so a partial failure is fixed by calling again.
    """
    import sqlite3
    from app.retrieval.vector_store import VectorStore

    status = registry.get(job_id)
    if status is not None and status.state not in ("ready", "failed"):
        raise HTTPException(
            409, f"batch '{job_id}' is still {status.state}; wait for it to finish, then delete it"
        )

    deleted = {"candidates": 0, "orphans": 0, "vectors": 0, "files": 0}
    problems: list[str] = []

    # --- 1. SQL, in one transaction ---
    db = DATA / "candidates.db"
    if db.exists():
        conn = sqlite3.connect(db)
        try:
            # Per connection, and before the transaction opens, or it is ignored.
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                cur = conn.execute("DELETE FROM candidates WHERE job_id = ?", (job_id,))
                deleted["candidates"] = max(cur.rowcount, 0)
                # Sweep child rows whose candidate no longer exists. With the
                # pragma on, this finds nothing new; it clears rows orphaned by
                # deletes made before foreign keys were enforced.
                for table in ("candidate_skills", "experience", "education"):
                    cur = conn.execute(
                        f"DELETE FROM {table} WHERE candidate_id NOT IN (SELECT id FROM candidates)"
                    )
                    deleted["orphans"] += max(cur.rowcount, 0)
        except sqlite3.Error as exc:
            problems.append(f"database: {exc}")
        finally:
            conn.close()

    # --- 2. vectors ---
    try:
        store = VectorStore()
        got = store.collection.get(where={"job_id": job_id}, include=[])
        if got["ids"]:
            store.collection.delete(ids=got["ids"])
            deleted["vectors"] = len(got["ids"])
    except Exception as exc:  # chroma raises several unrelated types
        problems.append(f"vector store: {type(exc).__name__}: {exc}")

    # --- 3. files ---
    for path in [chunks_path(job_id), DATA / f"extractions_{job_id}.json",
                 UPLOAD_DIR / f"{job_id}.zip"]:
        try:
            if path.exists():
                path.unlink()
                deleted["files"] += 1
        except OSError as exc:
            # On Windows a file still held open elsewhere cannot be removed.
            problems.append(f"{path.name}: {exc}")
    workdir = UPLOAD_DIR / job_id
    try:
        if workdir.is_dir():
            shutil.rmtree(workdir)
            deleted["files"] += 1
    except OSError as exc:
        problems.append(f"{workdir.name}/: {exc}")

    # BM25 is built from the chunk files that existed when the retriever was
    # first constructed. Drop it so the next ranking cannot cite deleted text.
    global _retriever
    _retriever = None

    if problems:
        # The job stays listed, so the recruiter can see it and try again.
        raise HTTPException(500, "batch only partly deleted - " + "; ".join(problems))

    found_anything = status is not None or any(
        deleted[k] for k in ("candidates", "vectors", "files")
    )
    if not found_anything:
        raise HTTPException(404, f"unknown batch '{job_id}'")

    registry.remove(job_id)
    return {"job_id": job_id, "deleted": deleted}


# ============================================================
# RANKING
# ============================================================

@app.post("/api/rank", response_model=RankResponse)
def rank(request: RankRequest) -> RankResponse:
    from app.ranking.jd_parser import parse_jd
    from app.ranking.scorer import CandidateScorer

    status = registry.get(request.job_id)
    if status and status.state != "ready":
        raise HTTPException(409, f"job '{request.job_id}' is {status.state}, not ready")

    started = time.time()
    try:
        jd = parse_jd(request.jd_text)
    except RuntimeError as exc:
        # A missing API key is a configuration fault, not a server crash. 503
        # with the reason is far more useful to whoever has to fix it than a
        # 500 and a stack trace in the log.
        raise HTTPException(503, f"LLM provider unavailable: {exc}")
    ranked = CandidateScorer(get_retriever()).rank(jd, job_id=request.job_id)

    # Likely-duplicate groups, across the whole batch rather than the shortlist
    # alone: a copy that was filtered out is still worth knowing about. Every
    # copy points at the same root (see app/db/duplicates.py), so grouping by
    # root is one pass.
    groups: dict[str, list] = {}
    for c in ranked:
        groups.setdefault(c.duplicate_of or c.resume_id, []).append(c)

    def duplicates_of(c) -> list[DuplicateRef]:
        return [
            DuplicateRef(resume_id=m.resume_id, source_file=m.source_file)
            for m in groups[c.duplicate_of or c.resume_id]
            if m.resume_id != c.resume_id
        ]

    def to_response(c, rank_position: Optional[int]) -> RankedCandidate:
        return RankedCandidate(
            rank=rank_position,
            resume_id=c.resume_id,
            source_file=c.source_file,
            duplicates=duplicates_of(c),
            name=c.name,
            years=c.years,
            highest_degree=c.highest_degree,
            score=round(c.score, 4),
            requirements_met=c.met,
            requirements_total=len(c.results),
            passed_filter=c.passed_filter,
            exclusion_reasons=c.filter_failures,
            evidence=[
                EvidenceItem(
                    requirement=r.requirement.text,
                    weight=r.requirement.weight,
                    score=round(r.score, 4),
                    source=r.source,
                    quote=r.evidence,
                )
                for r in sorted(c.results, key=lambda r: -r.weighted)
            ],
        )

    passed = [c for c in ranked if c.passed_filter]
    excluded = [c for c in ranked if not c.passed_filter]

    return RankResponse(
        job_id=request.job_id,
        title=jd.title,
        requirements=[
            ParsedRequirement(
                text=r.text, kind=r.kind.value, weight=r.weight,
                skill=r.canonical_skill, min_years=r.min_years,
                degree_level=r.degree_level,
            )
            for r in jd.requirements
        ],
        filter_spec=jd.filter_spec(),
        total_candidates=len(ranked),
        passed_filter=len(passed),
        shortlist=[to_response(c, i) for i, c in
                   enumerate(passed[:request.shortlist_size], start=1)],
        excluded=([to_response(c, None) for c in excluded]
                  if request.include_excluded else []),
        seconds=round(time.time() - started, 2),
    )


@app.post("/api/audit", response_model=AuditResponse)
def audit(request: RankRequest) -> AuditResponse:
    """Runs the ranking, then audits the resulting shortlist for disparate impact."""
    from app.fairness.audit import audit_shortlist
    from app.ranking.jd_parser import parse_jd
    from app.ranking.scorer import CandidateScorer

    try:
        jd = parse_jd(request.jd_text)
    except RuntimeError as exc:
        # A missing API key is a configuration fault, not a server crash. 503
        # with the reason is far more useful to whoever has to fix it than a
        # 500 and a stack trace in the log.
        raise HTTPException(503, f"LLM provider unavailable: {exc}")
    ranked = CandidateScorer(get_retriever()).rank(jd, job_id=request.job_id)
    audits = audit_shortlist(ranked, request.shortlist_size)

    return AuditResponse(
        job_id=request.job_id,
        shortlist_size=request.shortlist_size,
        total_candidates=len(ranked),
        attributes=[
            AttributeAuditResponse(
                attribute=a.attribute,
                description=a.description,
                impact_ratio=round(a.impact_ratio, 3) if a.impact_ratio else None,
                flagged=a.flagged,
                groups=[
                    GroupStat(value=g.value, total=g.total, selected=g.selected,
                              selection_rate=round(g.selection_rate, 3),
                              reliable=g.reliable)
                    for g in sorted(a.groups, key=lambda g: -g.selection_rate)
                ],
            )
            for a in audits
        ],
        note=("Impact ratios below 0.80 are flagged under the EEOC four-fifths "
              "rule. Groups with fewer than 10 members are excluded from ratios: "
              "at this size one shortlist decision moves a rate by over 10 "
              "points. A flag is a prompt to investigate, not a verdict."),
    )
@app.on_event("startup")
def seed_demo_corpus() -> None:
    """Rebuilds the demo batch when the filesystem is empty.

    Spaces without persistent storage reset on every restart, so the database
    and vector index have to be rebuilt. Everything needed is committed to the
    repo -- the synthetic resumes and their ground truth -- so this is a few
    minutes of CPU rather than a data loss problem. Real uploads made during a
    session are lost on restart, which is stated in the UI.
    """
    if (DATA / "candidates.db").exists():
        return

    import json
    from app.db.session import get_session, init_db
    from app.ingestion.batch import ingest_directory
    from app.retrieval.vector_store import VectorStore
    from scripts.load_database import load_record

    resumes = DATA / "sample_resumes"
    if not resumes.exists():
        print("no sample corpus committed - starting empty")
        return

    print("rebuilding demo corpus...")
    result = ingest_directory(resumes, job_id="demo")
    (DATA / "chunks_demo.json").write_text(
        json.dumps(result.chunks, ensure_ascii=False), encoding="utf-8"
    )
    VectorStore().index_chunks(result.chunks)

    extractions = DATA / "extractions_demo.json"
    if extractions.exists():
        init_db()
        with get_session() as session:
            for record in json.loads(extractions.read_text(encoding="utf-8")):
                load_record(session, record)
    print(f"demo corpus ready: {len(result.chunks)} chunks")  
FRONTEND = BASE.parent / "frontend" / "dist"

if FRONTEND.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        """Returns index.html for any non-API path.

        A single-page app does its own routing, so a browser refresh on
        /ranking must still be served the app shell rather than a 404.
        """
        return FileResponse(FRONTEND / "index.html")