import type { DuplicateRef, RankedCandidate } from '../api'
import { fileLabel } from '../lib/format'
import { IconAlert, IconFile } from './icons'

/**
 * WHICH FILE IS THIS PERSON?
 *
 * Names are not identifiers. Two rows reading "Viyom Shukla" with nothing to
 * tell them apart make a shortlist unusable, so every place a candidate is
 * named also carries the file they came from.
 *
 * The original filename, not resume_id: resume_id is only the stem, and
 * "Resume_Cloud.pdf" is what a recruiter with the folder open will search for.
 * resume_id is the fallback for records loaded before the filename was kept.
 *
 * Set small, mono and quiet. It is an identifier, not content, and must never
 * compete with the name for attention.
 */

type Identifiable = Pick<RankedCandidate, 'resume_id' | 'source_file'>

export function FileId({ candidate }: { candidate: Identifiable }) {
  return (
    <span
      className="inline-flex min-w-0 items-center gap-1 font-mono text-[11px] text-ink3"
      title={`File ${fileLabel(candidate)} (id ${candidate.resume_id})`}
    >
      <IconFile size={11} className="shrink-0" />
      <span className="truncate">{fileLabel(candidate)}</span>
    </span>
  )
}

const sameAs = (dups: DuplicateRef[]) => dups.map(fileLabel).join(', ')

/**
 * POSSIBLE DUPLICATE
 *
 * The backend flags candidates in the same batch with the same name AND email
 * (app/db/duplicates.py). The flag is a question for the recruiter, not a
 * conclusion: nothing is merged or hidden, so it is amber, not red.
 */
export function DuplicateMark({ duplicates }: { duplicates?: DuplicateRef[] }) {
  if (!duplicates?.length) return null
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-amber/40 bg-amber-soft px-1.5 py-px text-[10.5px] font-medium whitespace-nowrap text-amber"
      title={`Same name and email as ${sameAs(duplicates)}`}
    >
      <IconAlert size={11} />
      Possible duplicate
      <span className="sr-only">: same name and email as {sameAs(duplicates)}</span>
    </span>
  )
}

/** The spelled-out version, for places with room to say it: a tooltip is
 *  invisible on a touch screen, and this is the detail that matters. */
export function DuplicateNote({ duplicates }: { duplicates?: DuplicateRef[] }) {
  if (!duplicates?.length) return null
  return (
    <p className="mt-2 flex items-start gap-1.5 rounded-md border border-amber/35 bg-amber-soft px-2.5 py-1.5 text-[12px] text-ink">
      <IconAlert size={13} className="mt-[2px] text-amber" />
      <span>
        Possible duplicate. Same name and email as{' '}
        {duplicates.map((d, i) => (
          <span key={d.resume_id}>
            {i > 0 && ', '}
            <span className="font-mono text-[11.5px]">{fileLabel(d)}</span>
          </span>
        ))}
        . Check whether this is one person who applied more than once before giving them two
        places on the shortlist.
      </span>
    </p>
  )
}
