import type { FileStatus, JobState } from '../api'

export const pct = (n: number) => `${Math.round(n * 100)}%`

/** The original uploaded filename, falling back to resume_id (its stem) for
 *  records loaded before the filename was carried through. */
export const fileLabel = (c: { resume_id: string; source_file?: string | null }) =>
  c.source_file || c.resume_id

export const score3 = (n: number) => n.toFixed(3)

export function years(n: number): string {
  if (!Number.isFinite(n)) return '--'
  const v = Math.round(n * 10) / 10
  return `${v} yr${v === 1 ? '' : 's'}`
}

const DEGREES: Record<string, string> = {
  bachelor: "Bachelor's",
  master: "Master's",
  doctorate: 'Doctorate',
  none: 'Not stated',
  '': 'Not stated',
}

export const degree = (d: string | null | undefined) =>
  DEGREES[(d ?? '').toLowerCase()] ?? d ?? 'Not stated'

export function when(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// ============================================================
// JOB STATE
// ============================================================

export const JOB_STATE_LABEL: Record<JobState, string> = {
  queued: 'Queued',
  ingesting: 'Reading files',
  extracting: 'Extracting candidates',
  indexing: 'Indexing',
  ready: 'Ready',
  failed: 'Failed',
}

export const isTerminal = (s: JobState) => s === 'ready' || s === 'failed'

/** The pipeline in order, so progress can be shown as named stages rather than
 *  a percentage. `progress` is not linear: extraction takes minutes and
 *  indexing takes seconds, so a smooth bar looks broken. */
export const STAGES: JobState[] = ['queued', 'ingesting', 'extracting', 'indexing', 'ready']

// ============================================================
// PER-FILE OUTCOMES
//
// Every status except `parsed` means a resume did not fully make it into the
// batch. `empty_text` and `corrupt` matter most: that candidate exists, applied,
// and cannot be ranked. The recruiter has to know, so these are explained in
// full rather than shown as a bare status code.
// ============================================================

export type Tone = 'ok' | 'warn' | 'bad' | 'muted'

export type StatusMeta = {
  label: string
  tone: Tone
  /** What happened and what it means for the candidate. */
  note: string
}

export const FILE_STATUS: Record<string, StatusMeta> = {
  parsed: {
    label: 'Parsed',
    tone: 'ok',
    note: 'Text extracted and indexed. These candidates can be ranked.',
  },
  empty_text: {
    label: 'No text found',
    tone: 'bad',
    note:
      'The file opened but produced no readable text, almost always a scanned or photographed resume. These people applied and cannot be ranked. Ask them for a text-based file, or review them by hand.',
  },
  corrupt: {
    label: 'Unreadable file',
    tone: 'bad',
    note:
      'The file could not be opened. It may have been damaged in transit. These people applied and cannot be ranked. Request the file again.',
  },
  encrypted: {
    label: 'Password protected',
    tone: 'bad',
    note:
      'The file is locked and cannot be opened. These people cannot be ranked until they send an unprotected copy.',
  },
  unsupported_format: {
    label: 'Unsupported format',
    tone: 'warn',
    note: 'Only .pdf and .docx are read. Convert these files and upload them again.',
  },
  too_large: {
    label: 'Too large',
    tone: 'warn',
    note: 'The file exceeded the size limit and was skipped.',
  },
  duplicate: {
    label: 'Duplicate',
    tone: 'muted',
    note: 'The same resume appeared earlier in the batch. Counted once.',
  },
}

export const fileStatusMeta = (s: FileStatus | string): StatusMeta =>
  FILE_STATUS[s] ?? {
    label: s.replace(/_/g, ' '),
    tone: 'warn',
    note: 'This file was not added to the batch.',
  }

/** Only `parsed` resumes reach the ranker. */
export const isRankable = (s: FileStatus | string) => s === 'parsed'
