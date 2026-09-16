import { useState } from 'react'
import type { RankedCandidate } from '../api'
import { degree as degreeLabel, years as yearsLabel } from '../lib/format'
import { Chevron, MetCount, ScoreMark } from './Chrome'
import { EvidenceList } from './Evidence'

/**
 * A ledger of people, not a deck of cards.
 *
 * Rows are kept tight so roughly twenty candidates are visible at once. The
 * recruiter is comparing 45 people in an afternoon; a layout that shows six at
 * a time turns one comparison into six screens of scrolling.
 */

const GRID =
  'grid grid-cols-[28px_minmax(0,1fr)_96px_58px_14px] md:grid-cols-[34px_112px_minmax(0,1fr)_72px_116px_62px_14px] items-center gap-x-3'

export function CandidateHeader() {
  return (
    <div
      className={`${GRID} border-b border-rule bg-sunk/60 px-4 py-2 text-[11px] font-semibold tracking-[0.04em] text-ink3 uppercase`}
    >
      <div className="text-right">#</div>
      <div className="hidden md:block">Score</div>
      <div>Candidate</div>
      <div className="hidden text-right md:block">Years</div>
      <div className="hidden md:block">Degree</div>
      <div className="text-right md:hidden">Score</div>
      <div className="text-right">Met</div>
      <div />
    </div>
  )
}

function CandidateRow({
  candidate,
  open,
  onToggle,
}: {
  candidate: RankedCandidate
  open: boolean
  onToggle: () => void
}) {
  const panelId = `evidence-${candidate.resume_id}`
  const top = (candidate.rank ?? 99) <= 3

  return (
    <li className="border-b border-rule last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={panelId}
        className={`${GRID} w-full px-4 py-2.5 text-left transition-colors hover:bg-sunk/60 ${
          open ? 'bg-sunk/60' : ''
        }`}
      >
        <span
          className={`text-right font-mono text-[12.5px] tabular-nums ${
            top ? 'font-semibold text-accent' : 'text-ink3'
          }`}
        >
          {candidate.rank ?? '--'}
        </span>

        <span className="hidden md:block">
          <ScoreMark value={candidate.score} />
        </span>

        <span className="min-w-0">
          <span className="block truncate text-[14px] leading-[1.35] font-medium text-ink">
            {candidate.name}
          </span>
          {candidate.headline && (
            <span className="block truncate text-[11.5px] text-ink3">{candidate.headline}</span>
          )}
        </span>

        <span className="hidden text-right font-mono text-[12.5px] text-ink2 md:block">
          {yearsLabel(candidate.years)}
        </span>

        <span className="hidden truncate text-[12.5px] text-ink2 md:block">
          {degreeLabel(candidate.highest_degree)}
        </span>

        {/* Below md the bar is dropped and the figure alone is kept: a 40px
            bar is not readable, but the number still ranks the column. */}
        <span className="text-right font-mono text-[12.5px] tabular-nums text-ink md:hidden">
          {candidate.score.toFixed(3)}
        </span>

        <span className="text-right">
          <MetCount met={candidate.requirements_met} total={candidate.requirements_total} />
        </span>

        <span className="flex justify-end">
          <Chevron open={open} />
        </span>
      </button>

      {/* The one deliberate motion in the app: evidence opening on a click.
          grid-template-rows 0fr to 1fr animates to the panel's real height. */}
      <div className="drawer" data-open={open} id={panelId}>
        {/* inert while closed: a collapsed drawer is only visually hidden, so
            without it the content inside stays in the tab order and is read by
            screen readers. */}
        <div inert={!open}>
          <div className="border-t border-rule bg-paper px-4 pb-4 pl-10">
            <p className="flex flex-wrap items-baseline gap-x-2 pt-3 pb-1 text-[11.5px] text-ink3">
              Why this candidate scored
              <span className="font-mono text-ink2">{candidate.score.toFixed(3)}</span>, strongest
              evidence first.
              <span className="font-mono">{candidate.resume_id}</span>
            </p>
            {open && <EvidenceList evidence={candidate.evidence} />}
          </div>
        </div>
      </div>
    </li>
  )
}

export function CandidateTable({ candidates }: { candidates: RankedCandidate[] }) {
  const [openId, setOpenId] = useState<string | null>(null)
  return (
    <div>
      <CandidateHeader />
      <ul>
        {candidates.map((c) => (
          <CandidateRow
            key={c.resume_id}
            candidate={c}
            open={openId === c.resume_id}
            // One open at a time: the comparison is between rows, and three
            // open drawers push the next candidate off the screen.
            onToggle={() => setOpenId(openId === c.resume_id ? null : c.resume_id)}
          />
        ))}
      </ul>
    </div>
  )
}

/* ============================================================
   EXCLUDED
   Collapsed by default, but never absent. A filter that hides who it
   removed is how a wrong hard requirement goes unnoticed for months.
   ============================================================ */

export function ExcludedList({ excluded }: { excluded: RankedCandidate[] }) {
  const [open, setOpen] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)

  if (!excluded.length) return null

  return (
    <div className="card overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls="excluded-panel"
        className="flex w-full items-center gap-2.5 px-4 py-3 text-left transition-colors hover:bg-sunk/60"
      >
        <Chevron open={open} />
        <span className="text-[13.5px] font-semibold">
          {excluded.length} {excluded.length === 1 ? 'candidate was' : 'candidates were'} removed by
          the hard requirements
        </span>
        <span className="ml-auto hidden text-[11.5px] text-ink3 sm:inline">
          {open ? 'Hide' : 'Review who was removed and why'}
        </span>
      </button>

      <div className="drawer" data-open={open} id="excluded-panel">
        <div inert={!open}>
          <div className="border-t border-rule bg-paper px-4 pt-3 pb-4">
            <p className="pb-3 text-[12.5px] text-ink2">
              These people were filtered out before scoring. If someone here should have made it
              through, the hard requirement that removed them is wrong, not the candidate.
            </p>
            <ul className="border-t border-rule">
              {excluded.map((c) => {
                const isOpen = openId === c.resume_id
                const panelId = `excluded-${c.resume_id}`
                return (
                  <li key={c.resume_id} className="border-b border-rule">
                    <button
                      type="button"
                      onClick={() => setOpenId(isOpen ? null : c.resume_id)}
                      aria-expanded={isOpen}
                      aria-controls={panelId}
                      className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1.5 py-2.5 text-left"
                    >
                      <span className="flex min-w-0 flex-wrap items-baseline gap-x-2.5">
                        <span className="text-[13.5px] font-medium text-ink">{c.name}</span>
                        <span className="font-mono text-[11px] text-ink3">
                          {yearsLabel(c.years)}
                        </span>
                        <span className="text-[11px] text-ink3">
                          {degreeLabel(c.highest_degree)}
                        </span>
                      </span>
                      <span className="flex items-center gap-2">
                        <span className="text-[11px] text-ink3">
                          {c.evidence?.length ? 'Evidence' : ''}
                        </span>
                        <Chevron open={isOpen} />
                      </span>
                      <span className="col-span-2 flex flex-wrap gap-1.5">
                        {c.exclusion_reasons?.length ? (
                          c.exclusion_reasons.map((reason, i) => (
                            <span
                              key={i}
                              className="rounded-full border border-blood/30 bg-blood-soft px-2 py-[1px] text-[11px] text-blood"
                            >
                              {reason}
                            </span>
                          ))
                        ) : (
                          <span className="text-[11px] text-ink3">No reason recorded.</span>
                        )}
                      </span>
                    </button>
                    {(c.evidence?.length ?? 0) > 0 && (
                      <div className="drawer" data-open={isOpen} id={panelId}>
                        <div inert={!isOpen}>
                          <div className="pb-3 pl-4">
                            {isOpen && <EvidenceList evidence={c.evidence} />}
                          </div>
                        </div>
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}
