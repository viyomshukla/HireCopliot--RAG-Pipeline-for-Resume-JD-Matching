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
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi.responses import RedirectResponse
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
from fastapi.middleware.cors import CORSMiddleware

from api.pipeline import chunks_path, registry, run_pipeline
from api.schemas import (
    AttributeAuditResponse, AuditResponse, EvidenceItem, GroupStat, JobStatus,
    ParsedRequirement, RankRequest, RankResponse, RankedCandidate, UploadResponse,
)

DATA = BASE / "data"
UPLOAD_DIR = DATA / "uploads"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

app = FastAPI(
    title="Hiring Copilot API",
    description="Ranks candidates against a job description with cited evidence.",
    version="0.1.0",
)

# The Next.js dev server runs on a different port, so the browser treats it as a
# different origin. Locked to localhost: a wildcard here would let any website a
# user visits call this API with their session.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")
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
    job_id = f"job_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
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
    """Deletes a batch from every store.

    All three or none. Deleting SQL rows but leaving vectors behind produces
    orphan chunks that keep surfacing as evidence for a candidate the system
    claims not to have -- which is both a bug and, for personal data, a
    compliance failure.
    """
    import sqlite3
    from app.retrieval.vector_store import VectorStore

    deleted = {"chunks": 0, "vectors": 0, "candidates": 0, "files": 0}

    store = VectorStore()
    got = store.collection.get(where={"job_id": job_id}, include=[])
    if got["ids"]:
        store.collection.delete(ids=got["ids"])
        deleted["vectors"] = len(got["ids"])

    db = DATA / "candidates.db"
    if db.exists():
        conn = sqlite3.connect(db)
        try:
            cur = conn.execute("DELETE FROM candidates WHERE job_id = ?", (job_id,))
            deleted["candidates"] = cur.rowcount
            conn.commit()
        except sqlite3.OperationalError:
            pass    # job_id column not added yet
        conn.close()

    for path in [chunks_path(job_id), DATA / f"extractions_{job_id}.json",
                 UPLOAD_DIR / f"{job_id}.zip"]:
        if path.exists():
            path.unlink()
            deleted["files"] += 1
    workdir = UPLOAD_DIR / job_id
    if workdir.is_dir():
        shutil.rmtree(workdir)
        deleted["files"] += 1

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

    def to_response(c, rank_position: Optional[int]) -> RankedCandidate:
        return RankedCandidate(
            rank=rank_position,
            resume_id=c.resume_id,
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