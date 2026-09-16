import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  audit,
  type AttributeAudit,
  type AuditResponse,
  type GroupStat,
  type RankRequest,
} from '../api'
import { BusyBar, Callout, Chip, ErrorNote, Stat } from '../components/Chrome'
import { IconFairness, IconRank, IconRefresh, IconShield } from '../components/icons'

/**
 * DISPARATE IMPACT
 *
 * Three things this screen has to hold at once:
 *
 *   A flag is a prompt, not a verdict. A ratio under 0.80 says "look at this",
 *   and the copy and colour say that too. Nothing here is styled as an alarm.
 *
 *   An unreliable number must not read as a finding. Fewer than 10 people in a
 *   group makes the rate noise, and a hatched bar plus muted type keeps a 0.00
 *   from looking like discovered discrimination.
 *
 *   A missing ratio is not a good ratio. `impact_ratio: null` means no two
 *   groups were large enough to compare, and it gets its own treatment rather
 *   than an empty space that reads as "fine".
 */

const RATE_BAR = 132

/** Group values arrive as raw attribute values. Booleans come through as the
 *  strings "True" and "False", which read as nonsense under a heading like
 *  "career continuity", so they are phrased as an answer instead. Locale codes
 *  such as en_GB are meaningful as written and left alone. */
function groupLabel(value: string): string {
  if (value === 'True') return 'Yes'
  if (value === 'False') return 'No'
  if (/^[a-z]{2}_[A-Z]{2}$/.test(value)) return value
  return value.replace(/_/g, ' ')
}

function GroupRow({ group, best }: { group: GroupStat; best: number }) {
  const rate = group.selection_rate
  // Scaled against the highest rate in the panel, including unreliable groups.
  // Scaling to the reliable maximum alone overflows the bar whenever a small
  // group has the highest rate, which is common.
  const relative = best > 0 ? Math.min(rate / best, 1) : 0

  return (
    <tr className="border-t border-rule">
      <td className="py-2 pr-3">
        <span className={`text-[12.5px] ${group.reliable ? 'text-ink' : 'text-ink3'}`}>
          {groupLabel(group.value)}
        </span>
        {!group.reliable && (
          <span className="ml-2 text-[11px] text-ink3">too small to interpret</span>
        )}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-[12.5px] tabular-nums text-ink2">
        {group.total}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-[12.5px] tabular-nums text-ink2">
        {group.selected}
      </td>
      <td className="py-2 pr-3 text-right">
        <span
          className={`font-mono text-[12.5px] tabular-nums ${
            group.reliable ? 'text-ink' : 'text-ink3'
          }`}
        >
          {(rate * 100).toFixed(0)}%
        </span>
      </td>
      <td className="py-2">
        {/* A hatched bar for an unreliable group: it reads as "not usable data"
            rather than as a low value. */}
        <span
          className="block h-[8px] overflow-hidden rounded-full bg-sunk"
          style={{ width: RATE_BAR }}
          role="img"
          aria-label={`${group.selected} of ${group.total} selected, ${(rate * 100).toFixed(0)} percent${
            group.reliable ? '' : ', too small to interpret'
          }`}
        >
          <span
            className={`block h-full rounded-full ${
              group.reliable ? 'bg-accent' : 'hatch border border-rule-strong'
            }`}
            style={{ width: `${Math.max(relative, 0) * 100}%` }}
          />
        </span>
      </td>
    </tr>
  )
}

function AttributePanel({ attr }: { attr: AttributeAudit }) {
  const groups = attr.groups ?? []
  const unreliable = groups.filter((g) => !g.reliable)
  const best = Math.max(0, ...groups.map((g) => g.selection_rate))
  const ratio = attr.impact_ratio

  return (
    <section className="card overflow-hidden">
      <div className="border-b border-rule px-4 py-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h3 className="text-[14.5px] font-semibold capitalize">
            {attr.attribute.replace(/_/g, ' ')}
          </h3>
          {attr.flagged ? (
            <Chip tone="warn" dot>
              Worth investigating
            </Chip>
          ) : ratio == null ? (
            <Chip tone="muted" dot>
              Not enough data to compare
            </Chip>
          ) : (
            <Chip tone="ok" dot>
              Above the four-fifths threshold
            </Chip>
          )}
        </div>
        <p className="mt-1 text-[12.5px] text-ink2">{attr.description}</p>
      </div>

      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-rule bg-sunk/50 px-4 py-3">
        <span className="eyebrow">Impact ratio</span>
        {ratio == null ? (
          <span className="text-[13px] text-ink2">
            Cannot be calculated. No two groups here were large enough to compare, so this is an
            absence of evidence, not a clean result.
          </span>
        ) : (
          <>
            <span
              className={`font-mono text-[22px] leading-none tabular-nums ${
                attr.flagged ? 'text-amber' : 'text-ink'
              }`}
            >
              {ratio.toFixed(2)}
            </span>
            <span className="text-[11.5px] text-ink3">
              lowest selection rate divided by the highest, among groups of 10 or more
            </span>
          </>
        )}
      </div>

      {attr.flagged && (
        <div className="border-b border-rule bg-amber-soft px-4 py-3 text-[12.5px] leading-[1.55] text-ink">
          This is below 0.80, which is the point at which an employment screen is conventionally
          examined. It does not establish that anything is wrong. Look at whether a requirement is
          standing in for something the job does not actually need.
        </div>
      )}

      <div className="overflow-x-auto px-4 py-2">
        <table className="w-full">
          <thead>
            <tr className="text-[11px] tracking-[0.04em] text-ink3 uppercase">
              <th className="pb-1.5 text-left font-semibold">Group</th>
              <th className="pb-1.5 pr-3 text-right font-semibold">In batch</th>
              <th className="pb-1.5 pr-3 text-right font-semibold">Shortlisted</th>
              <th className="pb-1.5 pr-3 text-right font-semibold">Rate</th>
              <th className="pb-1.5 text-left font-semibold" style={{ width: RATE_BAR }} />
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <GroupRow key={g.value} group={g} best={best} />
            ))}
          </tbody>
        </table>
      </div>

      {unreliable.length > 0 && (
        <p className="border-t border-rule px-4 py-2.5 text-[11.5px] leading-[1.55] text-ink3">
          {unreliable.length} {unreliable.length === 1 ? 'group has' : 'groups have'} fewer than 10
          people and {unreliable.length === 1 ? 'was' : 'were'} left out of the ratio. At that size
          one shortlist decision moves the rate by more than 10 points, so the figure would describe
          the group size rather than the outcome.
        </p>
      )}
    </section>
  )
}

/* ============================================================
   VIEW
   ============================================================ */

export function FairnessView({
  jobId,
  jdText,
  shortlistSize,
  result,
  onResult,
  onRank,
}: {
  jobId: string | null
  jdText: string
  shortlistSize: number
  result: AuditResponse | null
  onResult: (r: AuditResponse) => void
  onRank: () => void
}) {
  const [running, setRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<ApiError | null>(null)
  const ticker = useRef<number | undefined>(undefined)

  // Reset happens in run(), the event that starts the clock, so the effect only
  // owns the interval.
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

  const run = async () => {
    if (!jobId) return
    setElapsed(0)
    setRunning(true)
    setError(null)
    const body: RankRequest = {
      job_id: jobId,
      jd_text: jdText,
      shortlist_size: shortlistSize,
      include_excluded: true,
    }
    try {
      onResult(await audit(body))
    } catch (err) {
      setError(err as ApiError)
    } finally {
      setRunning(false)
    }
  }

  if (!jobId || jdText.trim().length < 50) {
    return (
      <div className="mx-auto max-w-[880px]">
        <Callout
          tone="muted"
          title="Rank a shortlist first"
          actions={
            <button type="button" className="btn btn-sm" onClick={onRank}>
              <IconRank size={14} />
              Go to ranking
            </button>
          }
        >
          <p>
            The audit measures who ends up on a shortlist, so it needs a job description and a batch
            to run against. Go to the ranking step, then come back.
          </p>
        </Callout>
      </div>
    )
  }

  const attributes = result?.attributes ?? []
  const flagged = attributes.filter((a) => a.flagged)

  return (
    <div className="mx-auto max-w-[920px] space-y-5">
      <Callout tone="muted" title="What this screen measures">
        <p>
          It compares shortlist rates across groups the ranker never sees. A system can disadvantage
          a group without ever reading the attribute, because the things it does read correlate with
          it. That is why &ldquo;the model does not use this field&rdquo; is not a fairness claim,
          and why this screen exists.
        </p>
        <p className="mt-2">
          The attributes are proxies recorded alongside the resumes, not fields on anyone&rsquo;s
          application and not anything the ranking reads. Each one stands in for something a full
          audit would measure directly.
        </p>
      </Callout>

      {!result && !running && (
        <div className="card flex flex-col items-center px-6 py-10 text-center">
          <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl border border-rule bg-sunk text-accent">
            <IconFairness size={22} />
          </div>
          <h3 className="text-[16px] font-semibold">Run the audit on this shortlist</h3>
          <p className="mx-auto mt-1.5 max-w-[52ch] text-[13px] leading-[1.6] text-ink2">
            This re-runs the full ranking on the server and then measures the shortlist, so it takes
            about as long as ranking did.
          </p>
          <button type="button" className="btn btn-primary btn-lg mt-5" onClick={() => void run()}>
            <IconShield size={16} />
            Run the audit
          </button>
        </div>
      )}

      {running && (
        <div className="card overflow-hidden">
          <BusyBar />
          <div className="px-4 py-5">
            <p className="font-mono text-[26px] leading-none tabular-nums">{elapsed}s</p>
            <p className="mt-2 text-[12.5px] text-ink2">
              Ranking the batch again and then measuring the shortlist. Expect 15 to 60 seconds.
            </p>
          </div>
        </div>
      )}

      <ErrorNote error={error} onRetry={() => void run()} />

      {result && (
        <>
          <div className="card grid gap-5 px-4 py-4 sm:grid-cols-3">
            <Stat
              label="Attributes flagged"
              value={
                <>
                  {flagged.length}
                  <span className="text-ink3"> / {attributes.length}</span>
                </>
              }
              tone={flagged.length > 0 ? 'amber' : undefined}
              sub={
                flagged.length === 0
                  ? 'None below the four-fifths threshold'
                  : flagged.map((a) => a.attribute.replace(/_/g, ' ')).join(', ')
              }
            />
            <Stat label="Candidates" value={result.total_candidates} sub="In the batch" />
            <Stat label="Shortlist size" value={result.shortlist_size} sub="Selected by the ranker" />
          </div>

          <div className="space-y-4">
            {attributes.map((a) => (
              <AttributePanel key={a.attribute} attr={a} />
            ))}
          </div>

          {/* The methodological caveat, verbatim from the server. Paraphrasing
              it would quietly change what the numbers above are claiming. */}
          <blockquote className="rounded-lg border border-rule bg-sunk px-4 py-3.5">
            <p className="font-quote text-[14.5px] leading-[1.6] text-ink">{result.note}</p>
            <footer className="mt-2 text-[11px] text-ink3">
              Methodology note, as recorded by the audit.
            </footer>
          </blockquote>

          <button type="button" className="btn" onClick={() => void run()}>
            <IconRefresh size={15} />
            Run the audit again
          </button>
        </>
      )}
    </div>
  )
}
