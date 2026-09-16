import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, deleteJob, listJobs, type JobStatus } from '../api'
import { Chip, EmptyState, ErrorNote, SkeletonRows } from '../components/Chrome'
import {
  IconArchive,
  IconArrowRight,
  IconRefresh,
  IconSearch,
  IconTrash,
  IconUpload,
} from '../components/icons'
import { JOB_STATE_LABEL, isTerminal, when } from '../lib/format'

/**
 * THE BATCH LEDGER
 *
 * A table, not a deck of cards. Batches are compared on the same four figures
 * every time - how many files arrived, how many parsed, how many candidates
 * came out, and when - so they belong in aligned columns where a bad batch
 * shows up as a short number in a column of longer ones.
 */

const GRID =
  'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 md:grid-cols-[minmax(0,1.5fr)_118px_repeat(3,60px)_140px_auto]'

type Filter = 'all' | 'ready' | 'working' | 'failed'

const FILTERS: [Filter, string][] = [
  ['all', 'All'],
  ['ready', 'Ready'],
  ['working', 'Processing'],
  ['failed', 'Failed'],
]

function stateTone(state: JobStatus['state']) {
  if (state === 'ready') return 'ok' as const
  if (state === 'failed') return 'bad' as const
  return 'muted' as const
}

function matchesFilter(job: JobStatus, filter: Filter) {
  if (filter === 'all') return true
  if (filter === 'ready') return job.state === 'ready'
  if (filter === 'failed') return job.state === 'failed'
  return !isTerminal(job.state)
}

/* ============================================================
   ONE ROW
   ============================================================ */

function BatchRow({
  job,
  active,
  onOpen,
  onDeleted,
}: {
  job: JobStatus
  active: boolean
  onOpen: () => void
  onDeleted: () => void
}) {
  const [arming, setArming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const running = !isTerminal(job.state)

  const figures: [string, number][] = [
    ['files', job.n_files],
    ['parsed', job.n_parsed],
    ['people', job.n_candidates],
  ]

  return (
    <li className={`relative border-b border-rule last:border-b-0 ${active ? 'bg-accent-soft/35' : ''}`}>
      {/* The active batch is marked on the edge of the table rather than by
          tinting the whole row a second colour. */}
      {active && (
        <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[3px] bg-accent" />
      )}

      <div className={`${GRID} px-4 py-3`}>
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate font-mono text-[12.5px] text-ink">{job.job_id}</span>
            {active && <span className="shrink-0 text-[11px] font-medium text-accent">open</span>}
          </div>
          {job.state === 'failed' && job.error && (
            <p className="mt-1 line-clamp-2 font-mono text-[11.5px] text-blood">{job.error}</p>
          )}
          {running && job.stage_message && (
            <p className="mt-1 truncate text-[11.5px] text-ink2">{job.stage_message}</p>
          )}
          {running && (
            <div className="mt-1.5 h-[3px] w-full max-w-[220px] overflow-hidden rounded-full bg-sunk">
              <div
                className="h-full rounded-full bg-accent"
                style={{ width: `${Math.round(job.progress * 100)}%`, transition: 'width 400ms linear' }}
              />
            </div>
          )}
        </div>

        <div className="hidden md:block">
          <Chip tone={stateTone(job.state)} dot pulse={running}>
            {JOB_STATE_LABEL[job.state]}
          </Chip>
        </div>

        {figures.map(([label, value]) => (
          <div key={label} className="hidden text-right md:block">
            <span className="font-mono text-[13px] tabular-nums text-ink">{value}</span>
            <span className="ml-1 text-[11px] text-ink3">{label}</span>
          </div>
        ))}

        <div className="hidden text-[11.5px] text-ink3 md:block">{when(job.created_at)}</div>

        <div className="flex items-center justify-end gap-1.5">
          <button type="button" className="btn btn-sm" onClick={onOpen}>
            {job.state === 'ready' ? 'Rank' : 'Open'}
            <IconArrowRight size={14} />
          </button>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => setArming(true)}
            aria-label={`Delete batch ${job.job_id}`}
            title="Delete this batch"
          >
            <IconTrash size={15} />
          </button>
        </div>

        {/* On a narrow screen the status and figures fold underneath rather
            than being dropped, because a batch with 40 files and 3 parsed is
            exactly what a recruiter needs to notice on a phone too. */}
        <div className="col-span-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-ink3 md:hidden">
          <Chip tone={stateTone(job.state)} dot pulse={running}>
            {JOB_STATE_LABEL[job.state]}
          </Chip>
          {figures.map(([label, value]) => (
            <span key={label}>
              <span className="font-mono text-ink2">{value}</span> {label}
            </span>
          ))}
          <span>{when(job.created_at)}</span>
        </div>
      </div>

      {/* Deletion removes the resumes, the extracted candidates and the
          vectors, and cannot be undone, so it takes a second deliberate act
          and says what is about to be destroyed. */}
      {arming && (
        <div className="flex flex-wrap items-center gap-3 border-t border-blood/25 bg-blood-soft px-4 py-2.5">
          <span className="text-[12.5px] text-ink">
            Delete <span className="font-mono">{job.n_candidates}</span> candidates, every file and
            every vector in this batch? This cannot be undone.
          </span>
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              className="btn btn-sm btn-danger"
              disabled={busy}
              onClick={async () => {
                setBusy(true)
                setError(null)
                try {
                  await deleteJob(job.job_id)
                  onDeleted()
                } catch (err) {
                  setError((err as ApiError).message)
                  setBusy(false)
                  setArming(false)
                }
              }}
            >
              {busy ? 'Deleting...' : 'Yes, delete it'}
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy}
              onClick={() => setArming(false)}
            >
              Keep it
            </button>
          </div>
          {error && <span className="w-full text-[12px] text-blood">{error}</span>}
        </div>
      )}
    </li>
  )
}

/* ============================================================
   VIEW
   ============================================================ */

export function BatchesView({
  activeJobId,
  onOpen,
  onUpload,
}: {
  activeJobId: string | null
  onOpen: (jobId: string) => void
  onUpload: () => void
}) {
  const [jobs, setJobs] = useState<JobStatus[] | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')

  const load = useCallback(async () => {
    try {
      const next = await listJobs()
      setJobs(next)
      setError(null)
    } catch (err) {
      setError(err as ApiError)
      setJobs(null)
    }
  }, [])

  // Fetching the batch list on mount is synchronising with an external system,
  // which is what an effect is for. The rule fires because `load` is async and
  // it cannot see that every setState inside happens after the await.
  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  // A batch still processing keeps this list moving without a manual refresh,
  // and the interval is cleared the moment nothing is in flight.
  useEffect(() => {
    if (!jobs?.some((j) => !isTerminal(j.state))) return
    const id = window.setInterval(() => void load(), 4000)
    return () => window.clearInterval(id)
  }, [jobs, load])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (jobs ?? []).filter(
      (j) => matchesFilter(j, filter) && (!q || j.job_id.toLowerCase().includes(q)),
    )
  }, [jobs, filter, query])

  const counts = useMemo(() => {
    const all = jobs ?? []
    return {
      total: all.length,
      candidates: all.reduce((sum, j) => sum + j.n_candidates, 0),
      working: all.filter((j) => !isTerminal(j.state)).length,
    }
  }, [jobs])

  if (error) {
    return (
      <div className="mx-auto max-w-[1080px]">
        <ErrorNote error={error} onRetry={() => void load()} />
      </div>
    )
  }

  if (!jobs) {
    return (
      <div className="mx-auto max-w-[1080px]">
        <SkeletonRows rows={5} />
      </div>
    )
  }

  if (jobs.length === 0) {
    return (
      <div className="mx-auto max-w-[1080px]">
        <EmptyState
          icon={<IconArchive size={22} />}
          title="Start with a zip of resumes"
          action={
            <button type="button" className="btn btn-primary btn-lg" onClick={onUpload}>
              <IconUpload size={16} />
              Upload resumes
            </button>
          }
        >
          <p>
            Upload one archive and the server reads every resume into a searchable batch. Then
            paste a job description and rank them, with the evidence for every score shown next to
            the candidate it belongs to.
          </p>
        </EmptyState>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1080px] space-y-4">
      {/* --- toolbar --- */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[180px] flex-1 sm:max-w-[280px]">
          <IconSearch
            size={15}
            className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink3"
          />
          <input
            className="field pl-8 font-mono text-[12.5px]"
            placeholder="Find a batch id"
            aria-label="Filter batches by id"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        <div
          className="flex rounded-md border border-rule bg-sunk p-[2px]"
          role="group"
          aria-label="Filter by state"
        >
          {FILTERS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setFilter(key)}
              aria-pressed={filter === key}
              className={`rounded-[4px] px-2.5 py-1 text-[12.5px] transition-colors ${
                filter === key
                  ? 'bg-surface font-medium text-ink shadow-[var(--shadow-xs)]'
                  : 'text-ink2 hover:text-ink'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-3">
          <span className="hidden text-[12px] text-ink3 sm:inline">
            <span className="font-mono text-ink2">{counts.total}</span> batches,{' '}
            <span className="font-mono text-ink2">{counts.candidates}</span> candidates
            {counts.working > 0 && (
              <>
                , <span className="font-mono text-accent-mid">{counts.working}</span> processing
              </>
            )}
          </span>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => void load()}
            title="Refresh the list"
          >
            <IconRefresh size={15} />
            Refresh
          </button>
        </div>
      </div>

      {/* --- table --- */}
      <div className="card overflow-hidden">
        <div
          className={`${GRID} hidden border-b border-rule bg-sunk/60 px-4 py-2 text-[11px] font-semibold tracking-[0.04em] text-ink3 uppercase md:grid`}
        >
          <div>Batch</div>
          <div>Status</div>
          <div className="text-right">Files</div>
          <div className="text-right">Parsed</div>
          <div className="text-right">People</div>
          <div>Created</div>
          <div className="text-right">Actions</div>
        </div>

        {shown.length === 0 ? (
          <p className="px-4 py-10 text-center text-[13px] text-ink3">
            No batch matches that filter.
          </p>
        ) : (
          <ul>
            {shown.map((job) => (
              <BatchRow
                key={job.job_id}
                job={job}
                active={job.job_id === activeJobId}
                onOpen={() => onOpen(job.job_id)}
                onDeleted={() => void load()}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
