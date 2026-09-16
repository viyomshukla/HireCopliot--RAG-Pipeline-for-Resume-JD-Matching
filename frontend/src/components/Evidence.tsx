import type { ReactNode } from 'react'
import type { EvidenceItem } from '../api'
import { IconDatabase, IconQuote, IconX } from './icons'

/**
 * THE EVIDENCE LIST
 *
 * The whole product rests on this component. A score without its reasoning is
 * an instruction to trust the machine, which is exactly what this tool refuses
 * to ask for. So `source` is never flattened into a generic "evidence" row:
 *
 *   database   a confirmed structured fact from the extracted skills table.
 *              The claim is already established; the quote is only context.
 *   retrieval  a judgement the model made about the resume text. The quote IS
 *              the evidence, and gets the strongest typography on the page.
 *   missing    nothing was found. Shown, never hidden. An absent requirement
 *              is a finding in its own right.
 */

type SourceMeta = {
  label: string
  /** What this source means, in the recruiter's terms. */
  gloss: string
  chip: string
  icon: ReactNode
}

const SOURCE: Record<string, SourceMeta> = {
  database: {
    label: 'Confirmed',
    gloss: 'Recorded in the extracted skills table',
    chip: 'border-accent/35 bg-accent-soft text-accent',
    icon: <IconDatabase size={12} />,
  },
  retrieval: {
    label: 'Read from resume',
    gloss: 'A judgement about the resume text, quoted below',
    chip: 'border-rule-strong bg-surface text-ink2',
    icon: <IconQuote size={12} />,
  },
  missing: {
    label: 'Not found',
    gloss: 'Nothing in this resume matched the requirement',
    chip: 'border-dashed border-rule-strong bg-transparent text-ink3',
    icon: <IconX size={12} />,
  },
}

function Quote({ text }: { text: string }) {
  return (
    <blockquote className="mt-2 rounded-r-md border-l-2 border-accent/50 bg-accent-soft/35 py-1.5 pr-3 pl-3">
      <p className="font-quote text-[15px] leading-[1.55] text-ink">
        <span aria-hidden="true" className="text-ink3">
          &ldquo;
        </span>
        {text}
        <span aria-hidden="true" className="text-ink3">
          &rdquo;
        </span>
      </p>
    </blockquote>
  )
}

function Row({ item }: { item: EvidenceItem }) {
  const meta = SOURCE[item.source] ?? SOURCE.retrieval
  const hasQuote = typeof item.quote === 'string' && item.quote.trim().length > 0

  return (
    <li className="border-t border-rule py-3 first:border-t-0">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5">
        <span className="min-w-0 flex-1 text-[13px] leading-[1.5] text-ink">
          {item.requirement}
        </span>
        <div className="flex shrink-0 items-center gap-2.5">
          <span
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-[1px] text-[10.5px] font-medium ${meta.chip}`}
            title={meta.gloss}
          >
            {meta.icon}
            {meta.label}
          </span>
          <span className="font-mono text-[11px] text-ink3" title="Requirement weight">
            w{item.weight}
          </span>
          <span className="w-[34px] text-right font-mono text-[12.5px] tabular-nums text-ink">
            {item.score.toFixed(2)}
          </span>
        </div>
      </div>

      <div>
        {/* A database hit is already proven by the skills table. The quote is
            supporting context, so it is set as data, not as testimony. */}
        {item.source === 'database' && hasQuote && (
          <p className="mt-1.5 font-mono text-[11.5px] text-ink2">
            <span className="text-ink3">skills table: </span>
            {item.quote}
          </p>
        )}

        {/* A retrieval hit is only as good as the sentence it came from, so the
            sentence is the loudest thing here. */}
        {item.source === 'retrieval' && hasQuote && <Quote text={item.quote as string} />}

        {/* quote can be null when the reranker judged the best match too weak to
            show. Say so in words rather than rendering an empty box. */}
        {item.source === 'retrieval' && !hasQuote && (
          <p className="mt-1.5 text-[12px] text-ink3">
            Matched on the resume text, but no single passage was strong enough to quote.
          </p>
        )}

        {item.source === 'missing' && (
          <p className="mt-1.5 text-[12px] text-ink3">
            No supporting evidence found in this resume.
          </p>
        )}
      </div>
    </li>
  )
}

// `evidence` is optional because the backend declares it with a default, so the
// generated schema marks it not-required. Defaulting here keeps every call site
// from repeating the same guard.
export function EvidenceList({ evidence }: { evidence?: EvidenceItem[] }) {
  const items = evidence ?? []
  if (!items.length) {
    return (
      <p className="py-3 text-[12.5px] text-ink3">
        No requirements were scored for this candidate.
      </p>
    )
  }
  return (
    <ul className="rounded-lg border border-rule bg-surface px-3.5">
      {items.map((item, i) => (
        <Row key={`${item.requirement}-${i}`} item={item} />
      ))}
    </ul>
  )
}
