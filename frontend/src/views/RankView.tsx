import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  JD_MIN_LENGTH,
  rank,
  type FilterSpec,
  type RankRequest,
  type RankResponse,
  type RankedCandidate,
} from '../api'
import { CandidateTable, ExcludedList } from '../components/Candidates'
import { fileLabel } from '../lib/format'
import { BusyBar, Callout, Chip, ErrorNote, Stat } from '../components/Chrome'
import { RequirementsReview } from '../components/Requirements'
import { IconArrowRight, IconBatches, IconCheck, IconFairness } from '../components/icons'

type Step = 'jd' | 'review' | 'results'

/* ============================================================
   STEPPER
   Numbered because this genuinely is a sequence: the job description
   produces the rubric, and the rubric produces the ranking. Nothing
   else in the app is numbered.
   ============================================================ */

const STEP_LABELS: [Step, string, string][] = [
  ['jd', 'Job description', 'Paste the posting'],
  ['review', 'Requirements', 'Check the rubric'],
  ['results', 'Ranking', 'Read the evidence'],
]

function Stepper({ step, onGo }: { step: Step; onGo: (s: Step) => void }) {
  const index = STEP_LABELS.findIndex(([s]) => s === step)
  return (
    <ol className="card mb-6 flex flex-wrap items-stretch overflow-hidden p-0">
      {STEP_LABELS.map(([s, label, hint], i) => {
        const done = i < index
        const current = i === index
        return (
          <li key={s} className="min-w-[180px] flex-1 border-r border-rule last:border-r-0">
            <button
              type="button"
              disabled={!done}
              onClick={() => onGo(s)}
              aria-current={current ? 'step' : undefined}
              className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors ${
                current ? 'bg-accent-soft/60' : done ? 'hover:bg-sunk cursor-pointer' : ''
              }`}
            >
              <span
                aria-hidden="true"
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border font-mono text-[11.5px] ${
                  current
                    ? 'border-accent bg-accent text-accent-ink'
                    : done
                      ? 'border-moss/40 bg-moss-soft text-moss'
                      : 'border-rule-strong bg-surface text-ink3'
                }`}
              >
                {done ? <IconCheck size={12} /> : i + 1}
              </span>
              <span className="min-w-0">
                <span
                  className={`block truncate text-[13.5px] ${
                    current ? 'font-semibold text-ink' : done ? 'text-ink2' : 'text-ink3'
                  }`}
                >
                  {label}
                </span>
                <span className="block truncate text-[11.5px] text-ink3">{hint}</span>
              </span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

/* ============================================================
   RANKING PROGRESS

   /api/rank is a single request that does not stream progress, so
   there is no percentage to honestly report. What can be reported is
   elapsed time against the stages the server actually runs, which is
   more use than a spinner: after 40 seconds on a batch of 50, the
   recruiter can tell the difference between "still scoring" and
   "something is wrong".
   ============================================================ */

const RANK_STAGES = [
  { label: 'Reading the job description', hint: 'A language model turns it into requirements.' },
  { label: 'Applying the hard requirements', hint: 'Candidates who fail one are set aside.' },
  {
    label: 'Scoring the rest against each requirement',
    hint: 'The long stage. Searches and re-reads every resume.',
  },
]

function RankingProgress({ elapsed, count }: { elapsed: number; count: number | null }) {
  // Elapsed time is the measured value. The stage is an estimate from the
  // documented shape of the request, and is labelled as one.
  const likely = elapsed < 4 ? 0 : elapsed < 9 ? 1 : 2

  return (
    <div className="card overflow-hidden">
      <BusyBar />
      <div className="space-y-4 px-4 py-4">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-[26px] leading-none tabular-nums text-ink">
            {elapsed}s
          </span>
          <span className="text-[12.5px] text-ink2">
            Ranking {count != null ? `${count} candidates` : 'the batch'}. This usually takes
            between 15 and 60 seconds.
          </span>
        </div>

        <ol className="space-y-2">
          {RANK_STAGES.map((stage, i) => (
            <li key={stage.label} className="flex items-start gap-2.5">
              <span
                aria-hidden="true"
                className={`mt-[6px] h-[7px] w-[7px] shrink-0 rounded-full border ${
                  i < likely
                    ? 'border-moss bg-moss'
                    : i === likely
                      ? 'border-accent bg-accent'
                      : 'border-rule-strong bg-surface'
                }`}
                style={i === likely ? { animation: 'pulse-dot 1.4s ease-in-out infinite' } : undefined}
              />
              <span className="min-w-0">
                <span
                  className={`text-[13px] ${i === likely ? 'font-semibold text-ink' : 'text-ink2'}`}
                >
                  {stage.label}
                </span>
                <span className="ml-1.5 text-[11.5px] text-ink3">{stage.hint}</span>
              </span>
            </li>
          ))}
        </ol>

        <p className="border-t border-rule pt-3 text-[11.5px] text-ink3">
          The server runs this as one request and does not report its position, so the highlighted
          stage is an estimate from elapsed time. The count of seconds is measured.
        </p>
      </div>
    </div>
  )
}

/* ============================================================
   RESULT SUMMARY
   passed_filter of total_candidates, prominently. If 3 of 50 passed,
   the requirements are the thing to suspect, not the candidates.
   ============================================================ */

function Summary({ result, onReview }: { result: RankResponse; onReview: () => void }) {
  const { passed_filter, total_candidates, shortlist, seconds } = result
  const share = total_candidates > 0 ? passed_filter / total_candidates : 0
  const thin = total_candidates > 0 && share < 0.25

  return (
    <div className="space-y-3">
      <div className="card px-4 py-4">
        <div className="grid gap-5 sm:grid-cols-[minmax(0,1.6fr)_repeat(2,minmax(0,1fr))]">
          <div>
            <Stat
              label="Passed the hard requirements"
              value={
                <>
                  {passed_filter}
                  <span className="text-ink3"> / {total_candidates}</span>
                </>
              }
              tone={thin ? 'amber' : undefined}
            />
            <div
              className="mt-3 h-[6px] w-full overflow-hidden rounded-full bg-sunk"
              role="img"
              aria-label={`${passed_filter} of ${total_candidates} candidates passed the filter`}
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: `${share * 100}%`,
                  background: thin
                    ? 'var(--c-amber)'
                    : 'linear-gradient(90deg, var(--c-accent), var(--c-accent-bright))',
                }}
              />
            </div>
          </div>
          <Stat label="Shortlisted" value={shortlist?.length ?? 0} sub="Ranked into the list" />
          <Stat label="Ranked in" value={`${seconds}s`} sub="Server time for this request" />
        </div>
      </div>

      {thin && (
        <Callout
          tone="warn"
          title="Very few candidates got through the filter"
          actions={
            <button type="button" className="btn btn-sm" onClick={onReview}>
              Review the requirements
            </button>
          }
        >
          <p>
            {passed_filter} of {total_candidates} passed. When the filter removes this many people
            the usual cause is a hard requirement that was inferred too strictly, not a weak
            applicant pool. Check the rubric before you work from this shortlist.
          </p>
        </Callout>
      )}
    </div>
  )
}

/**
 * The same person twice on one shortlist takes a place someone else should
 * have had. Counted only where the copies are BOTH shortlisted: a copy that was
 * filtered out costs nothing, and is flagged on its own row regardless.
 */
function DuplicateWarning({ shortlist }: { shortlist: RankedCandidate[] }) {
  const onList = new Set(shortlist.map((c) => c.resume_id))
  const affected = shortlist.filter((c) => c.duplicates?.some((d) => onList.has(d.resume_id)))
  if (!affected.length) return null

  return (
    <Callout tone="warn" title={`${affected.length} shortlisted rows look like the same people`}>
      <p>
        These rows share a name and email with another row on this shortlist, which usually means
        one person uploaded more than once under different filenames:{' '}
        {affected.map((c, i) => (
          <span key={c.resume_id}>
            {i > 0 && ', '}
            <span className="font-medium">#{c.rank}</span>{' '}
            <span className="font-mono text-[12px]">{fileLabel(c)}</span>
          </span>
        ))}
        . Nothing has been merged. Check them before counting the shortlist.
      </p>
    </Callout>
  )
}

/** passed_filter can legitimately be 0. That is a real result, not an error,
 *  and it points at the requirements. */
function NobodyPassed({ result, onReview }: { result: RankResponse; onReview: () => void }) {
  return (
    <div className="rounded-lg border border-blood/30 bg-blood-soft px-5 py-6">
      <h3 className="text-[18px] font-semibold text-ink">No candidate met every hard requirement</h3>
      <p className="mt-2 max-w-[62ch] text-[13px] leading-[1.6] text-ink2">
        All {result.total_candidates} candidates in this batch failed at least one hard requirement,
        so there is nothing to rank. This is almost always the rubric rather than the applicants: a
        minimum years figure read too high, or a skill marked as required that the job description
        only preferred.
      </p>
      <button type="button" className="btn btn-primary mt-4" onClick={onReview}>
        Review the requirements
      </button>
      <p className="mt-3 text-[12px] text-ink2">
        Everyone who was removed is listed below with the reason, so you can see which requirement
        did it.
      </p>
    </div>
  )
}

/* ============================================================
   VIEW
   ============================================================ */

export function RankView({
  jobId,
  jdText,
  onJdText,
  shortlistSize,
  onShortlistSize,
  result,
  onResult,
  onAudit,
  onPickBatch,
}: {
  jobId: string | null
  jdText: string
  onJdText: (v: string) => void
  shortlistSize: number
  onShortlistSize: (n: number) => void
  result: RankResponse | null
  onResult: (r: RankResponse | null) => void
  onAudit: () => void
  onPickBatch: () => void
}) {
  const [step, setStep] = useState<Step>(result ? 'results' : 'jd')
  const [running, setRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<ApiError | null>(null)
  const ticker = useRef<number | undefined>(undefined)

  // The counter is reset by run(), the event that starts it, so this effect only
  // synchronises with the timer and never sets state on the way in.
  useEffect(() => {
    if (!running) return
    const started = Date.now()
    ticker.current = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    )
    return () => {
      if (ticker.current) window.clearInterval(ticker.current)
    }
  }, [running])

  const tooShort = jdText.trim().length < JD_MIN_LENGTH

  const run = async () => {
    if (!jobId || tooShort) return
    setElapsed(0)
    setRunning(true)
    setError(null)
    const body: RankRequest = {
      job_id: jobId,
      jd_text: jdText,
      shortlist_size: shortlistSize,
      // Always ask for the excluded candidates. A filter that silently deletes
      // people is exactly the failure this product exists to prevent.
      include_excluded: true,
    }
    try {
      const response = await rank(body)
      onResult(response)
      // Land on the rubric, not on the people. The requirements are what the
      // recruiter has to agree with before a ranked list of human beings is
      // worth anything.
      setStep('review')
    } catch (err) {
      setError(err as ApiError)
    } finally {
      setRunning(false)
    }
  }

  if (!jobId) {
    return (
      <div className="mx-auto max-w-[880px]">
        <Callout
          tone="muted"
          title="Pick a batch first"
          actions={
            <button type="button" className="btn btn-sm" onClick={onPickBatch}>
              <IconBatches size={14} />
              Go to batches
            </button>
          }
        >
          <p>
            Ranking runs against one uploaded batch of resumes. Upload a zip, or open an existing
            batch, and come back.
          </p>
        </Callout>
      </div>
    )
  }

  const filterSpec = (result?.filter_spec ?? {}) as FilterSpec

  return (
    <div className="mx-auto max-w-[1080px]">
      <Stepper step={step} onGo={setStep} />

      {/* ---------- 1. the job description ---------- */}
      {step === 'jd' && (
        <div className="max-w-[780px] space-y-4">
          <div className="card p-5">
            <label htmlFor="jd" className="label">
              Paste the job description
            </label>
            <textarea
              id="jd"
              className="field font-quote text-[14.5px] leading-[1.6]"
              rows={16}
              placeholder="Paste the full posting, including the responsibilities and the requirements."
              value={jdText}
              onChange={(e) => onJdText(e.target.value)}
            />
            <div className="mt-2 flex flex-wrap items-baseline justify-between gap-3 text-[11.5px] text-ink3">
              <span className="max-w-[54ch]">
                The system reads this into a list of requirements. You review that list before
                anyone is ranked.
              </span>
              <span className={`font-mono ${tooShort ? 'text-blood' : 'text-moss'}`}>
                {jdText.trim().length} / {JD_MIN_LENGTH} min
              </span>
            </div>

            <div className="mt-5 border-t border-rule pt-4">
              <label htmlFor="size" className="label">
                Shortlist size
              </label>
              <input
                id="size"
                type="number"
                min={1}
                max={500}
                className="field w-28 font-mono"
                value={shortlistSize}
                onChange={(e) => onShortlistSize(Number(e.target.value) || 1)}
              />
              <p className="hint">How many candidates to rank into the list.</p>
            </div>
          </div>

          <ErrorNote error={error} onRetry={() => void run()} />

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="btn btn-primary btn-lg"
              disabled={tooShort || running}
              onClick={() => void run()}
            >
              Read the job description
              <IconArrowRight size={15} />
            </button>
            <span className="text-[12px] text-ink3">
              {tooShort
                ? `Paste at least ${JD_MIN_LENGTH} characters.`
                : 'You will see the requirements before you see any candidate.'}
            </span>
          </div>

          {running && <RankingProgress elapsed={elapsed} count={null} />}
        </div>
      )}

      {/* ---------- 2. the rubric, before the people ---------- */}
      {step === 'review' && result && (
        <div className="space-y-5">
          <div>
            <h2 className="text-[16px] font-semibold">
              Check what the system decided you asked for
            </h2>
            <p className="mt-1 max-w-[78ch] text-[13px] leading-[1.6] text-ink2">
              This is the rubric it inferred from your job description. A hard requirement that was
              read wrongly removes qualified people, and this is where that gets caught. Nothing is
              shown about any individual until you confirm.
            </p>
          </div>

          <RequirementsReview
            requirements={result.requirements}
            filterSpec={filterSpec}
            title={result.title}
          />

          <div className="flex flex-wrap items-center gap-3 border-t border-rule pt-4">
            <button type="button" className="btn btn-primary" onClick={() => setStep('results')}>
              {result.passed_filter > 0
                ? `Show the ${result.shortlist?.length ?? 0} ranked candidates`
                : 'Show what the filter did'}
              <IconArrowRight size={15} />
            </button>
            <button type="button" className="btn" onClick={() => setStep('jd')}>
              Edit the job description
            </button>
            <span className="text-[12px] text-ink3">
              {result.passed_filter} of {result.total_candidates} candidates passed this filter.
            </span>
          </div>
        </div>
      )}

      {/* ---------- 3. the ranking ---------- */}
      {step === 'results' && result && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <h2 className="truncate text-[19px] font-semibold">
                {result.title || 'Ranked candidates'}
              </h2>
              <Chip tone="muted">{result.shortlist?.length ?? 0} shortlisted</Chip>
            </div>
            <div className="flex gap-2">
              <button type="button" className="btn btn-sm" onClick={() => setStep('review')}>
                Requirements
              </button>
              <button type="button" className="btn btn-sm" onClick={onAudit}>
                <IconFairness size={14} />
                Check for bias
              </button>
            </div>
          </div>

          <Summary result={result} onReview={() => setStep('review')} />

          <DuplicateWarning shortlist={result.shortlist ?? []} />

          {result.passed_filter === 0 ? (
            <NobodyPassed result={result} onReview={() => setStep('review')} />
          ) : (
            <div className="card overflow-hidden">
              <div className="card-head">
                <p className="text-[12.5px] text-ink2">
                  Ranked by how well the evidence matches the requirements. Open a row to read the
                  evidence behind the score.
                </p>
                <span className="hidden shrink-0 text-[11.5px] text-ink3 lg:inline">
                  The tool ranks and cites. The decision is yours.
                </span>
              </div>
              <CandidateTable candidates={result.shortlist ?? []} />
            </div>
          )}

          <ExcludedList excluded={result.excluded ?? []} />
        </div>
      )}
    </div>
  )
}
