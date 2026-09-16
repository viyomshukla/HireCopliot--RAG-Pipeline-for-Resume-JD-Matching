import { useRef, useState } from 'react'
import { ApiError, uploadZip, type JobStatus } from '../api'
import { BusyBar, Callout, Chip, ErrorNote, Stat } from '../components/Chrome'
import { FileReportPanel } from '../components/FileReport'
import { IconArchive, IconArrowRight, IconCheck, IconUpload } from '../components/icons'
import { JOB_STATE_LABEL, STAGES, when } from '../lib/format'

/* ============================================================
   PROGRESS
   ============================================================ */

function StageTrack({ job }: { job: JobStatus }) {
  const failed = job.state === 'failed'
  const complete = job.state === 'ready'
  const current = STAGES.indexOf(job.state)

  return (
    <ol className="flex flex-wrap items-center gap-x-1 gap-y-2">
      {STAGES.map((stage, i) => {
        // On `ready` every stage is behind us, the last one included. Leaving
        // the final marker hollow reads as "not there yet" on a finished job.
        const done = !failed && (complete || current > i)
        const active = !failed && !complete && current === i
        return (
          <li key={stage} className="flex items-center gap-1">
            <span
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] ${
                active
                  ? 'border-accent bg-accent-soft font-semibold text-accent'
                  : done
                    ? 'border-rule bg-surface text-ink2'
                    : 'border-dashed border-rule bg-transparent text-ink3'
              }`}
            >
              {done ? (
                <IconCheck size={12} className="text-moss" />
              ) : (
                <span
                  aria-hidden="true"
                  className={`h-[6px] w-[6px] rounded-full ${active ? 'bg-accent' : 'bg-rule-strong'}`}
                  style={active ? { animation: 'pulse-dot 1.4s ease-in-out infinite' } : undefined}
                />
              )}
              {JOB_STATE_LABEL[stage]}
            </span>
            {i < STAGES.length - 1 && (
              <span
                aria-hidden="true"
                className={`h-px w-3 ${done ? 'bg-rule-strong' : 'bg-rule'}`}
              />
            )}
          </li>
        )
      })}
    </ol>
  )
}

function Counts({ job }: { job: JobStatus }) {
  const items: [string, number][] = [
    ['Files', job.n_files],
    ['Parsed', job.n_parsed],
    ['Passages', job.n_chunks],
    ['Candidates', job.n_candidates],
  ]
  return (
    <div className="grid grid-cols-2 gap-4 border-t border-rule pt-4 sm:grid-cols-4">
      {items.map(([label, value]) => (
        <Stat key={label} label={label} value={value} />
      ))}
    </div>
  )
}

function Progress({
  job,
  polling,
  onRank,
}: {
  job: JobStatus
  polling: boolean
  onRank: () => void
}) {
  const failed = job.state === 'failed'
  const ready = job.state === 'ready'

  return (
    <div className="space-y-5">
      <div className="card overflow-hidden">
        <div className="card-head">
          <span className="truncate font-mono text-[12px] text-ink2">{job.job_id}</span>
          <div className="flex shrink-0 items-center gap-3">
            <span className="hidden text-[11.5px] text-ink3 sm:inline">
              Started {when(job.created_at)}
            </span>
            <Chip
              tone={failed ? 'bad' : ready ? 'ok' : 'muted'}
              dot
              pulse={!failed && !ready}
            >
              {JOB_STATE_LABEL[job.state]}
            </Chip>
          </div>
        </div>

        {polling && <BusyBar />}

        <div className="space-y-4 px-4 py-4">
          <StageTrack job={job} />

          {/* The stage message is the primary signal, not the percentage.
              `progress` jumps unevenly because extraction takes minutes while
              indexing takes seconds, so a smooth bar would read as broken. */}
          <p className="font-quote text-[17px] leading-snug text-ink">
            {job.stage_message || JOB_STATE_LABEL[job.state]}
            {polling && <span className="ml-1 text-ink3">...</span>}
          </p>

          {/* The bar is only meaningful while work is outstanding. Once the job
              is ready, a full bar captioned "extraction holds here for minutes"
              describes something that already finished. */}
          {!failed && !ready && (
            <div>
              <div className="flex items-baseline justify-between gap-3">
                <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-sunk">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.round(job.progress * 100)}%`,
                      background: 'linear-gradient(90deg, var(--c-accent), var(--c-accent-bright))',
                      transition: 'width 400ms linear',
                    }}
                  />
                </div>
                <span className="font-mono text-[12.5px] tabular-nums text-ink2">
                  {Math.round(job.progress * 100)}%
                </span>
              </div>
              <p className="mt-1.5 text-[11.5px] text-ink3">
                Extraction is the long stage and holds here for minutes, so watch the stage above
                rather than the bar.
              </p>
            </div>
          )}

          <Counts job={job} />
        </div>
      </div>

      {failed && (
        <Callout tone="bad" title="This batch failed">
          <p className="font-mono text-[12px]">{job.error || 'No error detail was recorded.'}</p>
          <p className="mt-2">
            Nothing from this upload was indexed. Check the zip opens on your machine and contains
            .pdf or .docx files at the top level, then upload it again. If the message mentions an
            API key or a provider, the server needs configuring before any batch will process.
          </p>
        </Callout>
      )}

      {ready && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-moss/30 bg-moss-soft px-4 py-3">
          <IconCheck size={18} className="text-moss" />
          <span className="text-[13px] text-ink">
            <span className="font-mono">{job.n_candidates}</span> candidates are indexed and ready
            to rank.
          </span>
          <button type="button" className="btn btn-primary ml-auto" onClick={onRank}>
            Rank these candidates
            <IconArrowRight size={15} />
          </button>
        </div>
      )}

      {ready && <FileReportPanel files={job.files} />}
    </div>
  )
}

/* ============================================================
   DROP ZONE
   ============================================================ */

function DropZone({
  file,
  onFile,
  disabled,
}: {
  file: File | null
  onFile: (f: File | null) => void
  disabled: boolean
}) {
  const [over, setOver] = useState(false)
  const [reject, setReject] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  const accept = (f: File | undefined) => {
    if (!f) return
    if (!f.name.toLowerCase().endsWith('.zip')) {
      setReject(`${f.name} is not a .zip. Put the resumes in a zip archive and try again.`)
      return
    }
    setReject(null)
    onFile(f)
  }

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault()
          if (!disabled) setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          if (!disabled) accept(e.dataTransfer.files?.[0])
        }}
        className={`rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          over ? 'border-accent bg-accent-soft' : 'border-rule-strong bg-surface'
        }`}
      >
        <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-rule bg-sunk text-accent">
          <IconArchive size={21} />
        </div>
        <p className="text-[16px] font-semibold text-ink">Drop a zip of resumes here</p>
        <p className="mt-1 text-[12.5px] text-ink2">
          One archive of .pdf and .docx files, up to 200MB.
        </p>
        <button
          type="button"
          className="btn mt-4"
          disabled={disabled}
          onClick={() => input.current?.click()}
        >
          Choose a file
        </button>
        <input
          ref={input}
          type="file"
          accept=".zip,application/zip"
          className="sr-only"
          onChange={(e) => accept(e.target.files?.[0])}
        />
        {file && (
          <p className="mx-auto mt-4 flex w-fit items-center gap-2 rounded-md border border-rule bg-sunk px-3 py-1.5 font-mono text-[12px] text-ink">
            <IconArchive size={14} className="text-ink3" />
            {file.name}
            <span className="text-ink3">{(file.size / 1_000_000).toFixed(1)}MB</span>
          </p>
        )}
      </div>
      {reject && (
        <div className="mt-3">
          <Callout tone="bad">{reject}</Callout>
        </div>
      )}
    </div>
  )
}

/* ============================================================
   VIEW
   ============================================================ */

export function UploadView({
  company,
  onCompany,
  activeJobId,
  job,
  pollError,
  polling,
  onJobStarted,
  onRank,
}: {
  company: string
  onCompany: (v: string) => void
  activeJobId: string | null
  /** Polled once, in App, so the shell and this screen read the same status. */
  job: JobStatus | null
  pollError: ApiError | null
  polling: boolean
  onJobStarted: (jobId: string) => void
  onRank: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  const submit = async () => {
    if (!file) return
    setSending(true)
    setError(null)
    try {
      // 202 Accepted. This resolving means the server took the file, NOT that
      // the resumes are processed. The work runs in a background task for
      // minutes afterwards, which is what the polling above is for.
      const accepted = await uploadZip(file)
      setFile(null)
      onJobStarted(accepted.job_id)
    } catch (err) {
      setError(err as ApiError)
    } finally {
      setSending(false)
    }
  }

  if (activeJobId) {
    return (
      <div className="mx-auto max-w-[880px] space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-[15px] font-semibold">Processing</h2>
          <span className="text-[12px] text-ink3">
            {polling ? 'Checking every 2 seconds' : 'Finished, no longer polling'}
          </span>
        </div>

        {pollError && (
          <ErrorNote
            error={
              pollError.status === 404
                ? {
                    message: `The server has no record of ${activeJobId}. Job status is held in memory, so restarting the backend loses it. Upload the zip again.`,
                    status: 404,
                  }
                : pollError
            }
          />
        )}

        {job ? (
          <Progress job={job} polling={polling} onRank={onRank} />
        ) : (
          !pollError && (
            <div className="card overflow-hidden">
              <BusyBar />
              <p className="px-4 py-6 text-[13px] text-ink3">Asking the server for this batch...</p>
            </div>
          )
        )}
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[760px] space-y-5">
      <div className="card p-5">
        <label htmlFor="company" className="label">
          Hiring for
        </label>
        {/*
          COMPANY NAME: SESSION STATE ONLY, ON PURPOSE.

          This value is held in React state and nowhere else. It is never sent
          to the backend, never written to localStorage, and never put in the
          URL. That is a deliberate privacy choice, not an oversight or a
          missing feature.

          The backend has no field for it and should not acquire one: the
          moment a company name is stored next to a batch of resumes, a pile of
          CVs becomes an identifiable record of who applied where, which is a
          materially different thing to hold.

          It is a label for this session. It disappears on refresh, and that is
          the intended behaviour. Please do not "fix" it by persisting it.
        */}
        <input
          id="company"
          className="field"
          placeholder="Northwind Systems"
          value={company}
          onChange={(e) => onCompany(e.target.value)}
        />
        <p className="hint">
          Shown as a label while you work. Held in this browser tab only, never sent to the server
          or saved, and cleared when you refresh.
        </p>
      </div>

      <DropZone file={file} onFile={setFile} disabled={sending} />

      <ErrorNote error={error} onRetry={() => void submit()} />

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="btn btn-primary btn-lg"
          disabled={!file || sending}
          onClick={() => void submit()}
        >
          <IconUpload size={16} />
          {sending ? 'Uploading...' : 'Upload and process'}
        </button>
        <span className="text-[12px] text-ink3">
          Processing runs on the server and takes several minutes. You can watch it here.
        </span>
      </div>
    </div>
  )
}
