import { useState } from 'react'
import type { FileReport as FileReportRow } from '../api'
import { fileStatusMeta, isRankable } from '../lib/format'
import { Chevron } from './Chrome'
import { IconFile } from './icons'

/**
 * WHAT ARRIVED, AND WHAT DID NOT
 *
 * Grouped by outcome rather than listed by filename, because the only question
 * worth answering here is "whose resume failed to make it in". A scanned PDF
 * that yielded no text means a real applicant cannot be ranked at all, and that
 * has to be visible and explained rather than buried as a status code in a list
 * of fifty rows.
 */

function Group({
  status,
  rows,
  total,
  defaultOpen,
}: {
  status: string
  rows: FileReportRow[]
  total: number
  defaultOpen: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const meta = fileStatusMeta(status)
  const panelId = `files-${status}`

  const bar =
    meta.tone === 'bad'
      ? 'bg-blood'
      : meta.tone === 'warn'
        ? 'bg-amber'
        : meta.tone === 'ok'
          ? 'bg-moss'
          : 'bg-rule-strong'

  const share = total > 0 ? (rows.length / total) * 100 : 0

  return (
    <div className="border-b border-rule last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={panelId}
        className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-sunk/60"
      >
        <Chevron open={open} />
        <span aria-hidden="true" className={`h-3.5 w-[3px] shrink-0 rounded-full ${bar}`} />
        <span className="text-[13px] font-medium">{meta.label}</span>
        <span className="font-mono text-[12px] text-ink3">{rows.length}</span>

        {/* A proportion bar: "12 unreadable" means something different in a
            batch of 15 than in a batch of 400. */}
        <span
          aria-hidden="true"
          className="ml-auto hidden h-[5px] w-24 overflow-hidden rounded-full bg-sunk sm:block"
        >
          <span className={`block h-full rounded-full ${bar}`} style={{ width: `${share}%` }} />
        </span>
        {!isRankable(status) && (
          <span className="shrink-0 text-[11.5px] text-ink3">not ranked</span>
        )}
      </button>

      <div className="drawer" data-open={open} id={panelId}>
        <div inert={!open}>
          <div className="bg-paper px-4 pb-3 pl-[42px]">
            <p className="py-2.5 text-[12.5px] leading-[1.55] text-ink2">{meta.note}</p>
            <ul className="border-t border-rule">
              {rows.map((row, i) => (
                <li
                  key={`${row.name}-${i}`}
                  className="flex items-baseline justify-between gap-3 border-b border-rule py-1.5 last:border-b-0"
                >
                  <span className="flex min-w-0 items-baseline gap-2">
                    <IconFile size={12} className="shrink-0 translate-y-[1px] text-ink3" />
                    <span className="truncate font-mono text-[11.5px] text-ink">{row.name}</span>
                  </span>
                  <span className="shrink-0 text-[11px] text-ink3">
                    {row.detail
                      ? row.detail
                      : row.n_chunks > 0
                        ? `${row.n_chunks} passages indexed`
                        : ''}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}

export function FileReportPanel({ files }: { files?: FileReportRow[] }) {
  if (!files?.length) return null

  const groups = new Map<string, FileReportRow[]>()
  for (const f of files) {
    const list = groups.get(f.status) ?? []
    list.push(f)
    groups.set(f.status, list)
  }

  // Problems first. The parsed files are the ones nobody needs to look at.
  const order = [...groups.entries()].sort((a, b) => {
    const rank = (s: string) => (isRankable(s) ? 2 : fileStatusMeta(s).tone === 'muted' ? 1 : 0)
    return rank(a[0]) - rank(b[0])
  })

  const unusable = files.filter((f) => !isRankable(f.status)).length

  return (
    <div className="card overflow-hidden">
      <div className="card-head">
        <h3 className="text-[13.5px] font-semibold">Files in this batch</h3>
        <span className={`text-[11.5px] ${unusable > 0 ? 'text-amber' : 'text-ink3'}`}>
          {unusable > 0
            ? `${files.length - unusable} of ${files.length} can be ranked`
            : `all ${files.length} can be ranked`}
        </span>
      </div>
      {order.map(([status, rows]) => (
        <Group
          key={status}
          status={status}
          rows={rows}
          total={files.length}
          // Anything that failed opens by default. Success stays folded away.
          defaultOpen={!isRankable(status) && fileStatusMeta(status).tone === 'bad'}
        />
      ))}
    </div>
  )
}
