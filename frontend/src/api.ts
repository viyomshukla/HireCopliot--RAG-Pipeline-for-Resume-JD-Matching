/**
 * The only place that talks to the backend.
 *
 * Every response type is taken from src/api-types.ts, which is generated from
 * the running server:
 *
 *     npx openapi-typescript http://localhost:8000/openapi.json -o src/api-types.ts
 *
 * Regenerate it rather than hand-editing, so a backend change shows up as a
 * TypeScript error instead of a runtime surprise.
 */
import type { components } from './api-types'

/** The one base URL. The backend's CORS allowlist only admits an app served
 *  from port 3000, which is why vite.config.ts pins that port. */
export const API_BASE = 'http://localhost:8000'

type S = components['schemas']

export type JobStatus = S['JobStatus']
export type JobState = JobStatus['state']
export type FileReport = S['FileReport']
export type FileStatus = FileReport['status']
export type UploadResponse = S['UploadResponse']
export type RankRequest = S['RankRequest']
export type RankResponse = S['RankResponse']
export type RankedCandidate = S['RankedCandidate']
export type DuplicateRef = S['DuplicateRef']
export type EvidenceItem = S['EvidenceItem']
export type ParsedRequirement = S['ParsedRequirement']
export type AuditResponse = S['AuditResponse']
export type AttributeAudit = S['AttributeAuditResponse']
export type GroupStat = S['GroupStat']

/** filter_spec is typed as a bare object by FastAPI because the backend builds
 *  it as a plain dict. This is its actual shape, per JD.filter_spec(). */
export type FilterSpec = {
  min_years: number | null
  min_degree: string | null
  required_skills: string[]
}

/**
 * An API failure carrying enough detail to act on.
 *
 * `offline` distinguishes "the browser could not reach the server at all"
 * from "the server answered with an error". The first is nearly always the
 * backend not running, or a CORS rejection, and deserves different advice.
 */
export class ApiError extends Error {
  status: number
  offline: boolean
  constructor(message: string, status: number, offline = false) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.offline = offline
  }
}

/** FastAPI reports errors as {detail: string} or, for validation failures,
 *  {detail: [{loc, msg, type}]}. Flatten both into one readable sentence. */
function readDetail(body: unknown, fallback: string): string {
  if (typeof body !== 'object' || body === null) return fallback
  const detail = (body as { detail?: unknown }).detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (typeof d !== 'object' || d === null) return null
        const e = d as { loc?: unknown[]; msg?: string }
        const field = Array.isArray(e.loc) ? e.loc.slice(1).join('.') : ''
        return field ? `${field}: ${e.msg ?? ''}`.trim() : (e.msg ?? null)
      })
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  return fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, init)
  } catch {
    // fetch only rejects for network-level failures. With this backend that
    // means it is not running on port 8000, or the origin was refused by CORS.
    throw new ApiError(
      `Cannot reach the API at ${API_BASE}. Check the backend is running, and that this page is served from port 3000 (the backend only allows that origin).`,
      0,
      true,
    )
  }

  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      /* an error body that is not JSON; fall through to the status text */
    }
    throw new ApiError(
      readDetail(body, `${response.status} ${response.statusText}`),
      response.status,
    )
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// ============================================================
// JOBS
// ============================================================

export const listJobs = () => request<JobStatus[]>('/api/jobs')

export const getJob = (jobId: string) =>
  request<JobStatus>(`/api/jobs/${encodeURIComponent(jobId)}`)

export const deleteJob = (jobId: string) =>
  request<{ job_id: string; deleted: Record<string, number> }>(
    `/api/jobs/${encodeURIComponent(jobId)}`,
    { method: 'DELETE' },
  )

/**
 * Returns 202 Accepted, NOT a finished result.
 *
 * The response carries a job_id and nothing else of substance. Processing runs
 * in a background task on the server and takes minutes. The caller must poll
 * getJob(); treating this promise resolving as "the upload is done" is the
 * single easiest bug to write against this API.
 */
export function uploadZip(file: File, jobId?: string): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)
  if (jobId) form.append('job_id', jobId)
  // No Content-Type header: the browser must set the multipart boundary itself.
  return request<UploadResponse>('/api/upload', { method: 'POST', body: form })
}

// ============================================================
// RANKING
// ============================================================

export const rank = (body: RankRequest) => postJson<RankResponse>('/api/rank', body)

export const audit = (body: RankRequest) => postJson<AuditResponse>('/api/audit', body)

/** The backend requires at least 50 characters of job description
 *  (RankRequest.jd_text, min_length=50). Checked here so the recruiter gets a
 *  sentence instead of a 422. */
export const JD_MIN_LENGTH = 50
