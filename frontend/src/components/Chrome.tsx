import type { ReactNode } from 'react'
import type { Tone } from '../lib/format'
import { IconAlert, IconCheck, IconInfo } from './icons'

/* ============================================================
   SCORE MARK
   0.856 means nothing at a glance. A proportional bar next to the
   figure makes a column of 45 scores scannable in one pass, which is
   the actual task. Mono digits keep the decimal points aligned.
   ============================================================ */

export function ScoreMark({ value, width = 58 }: { value: number; width?: number }) {
  const clamped = Math.max(0, Math.min(1, value))
  return (
    <span className="inline-flex items-center gap-2" title={`Score ${value.toFixed(3)} of 1.000`}>
      <span
        role="img"
        aria-label={`Score ${value.toFixed(3)} out of 1`}
        className="relative block h-[6px] shrink-0 overflow-hidden rounded-full bg-sunk"
        style={{ width }}
      >
        <span
          className="absolute inset-y-0 left-0 rounded-full"
          style={{
            width: `${clamped * 100}%`,
            background: 'linear-gradient(90deg, var(--c-accent), var(--c-accent-bright))',
          }}
        />
      </span>
      <span className="font-mono text-[12.5px] tabular-nums text-ink">{value.toFixed(3)}</span>
    </span>
  )
}

/* ============================================================
   MET / TOTAL
   ============================================================ */

export function MetCount({ met, total }: { met: number; total: number }) {
  const short = total > 0 && met / total < 0.5
  return (
    <span className="font-mono text-[12.5px] tabular-nums">
      <span className={short ? 'text-blood' : 'text-ink'}>{met}</span>
      <span className="text-ink3">/{total}</span>
    </span>
  )
}

/* ============================================================
   CHIP
   A small status token. Rounded, hairline, never a filled pill in a
   loud colour: nothing in this app is important enough to shout.
   ============================================================ */

const CHIP_TONE: Record<Tone, string> = {
  ok: 'border-moss/35 bg-moss-soft text-moss',
  warn: 'border-amber/40 bg-amber-soft text-amber',
  bad: 'border-blood/35 bg-blood-soft text-blood',
  muted: 'border-rule-strong bg-sunk text-ink2',
}

const CHIP_DOT: Record<Tone, string> = {
  ok: 'bg-moss',
  warn: 'bg-amber',
  bad: 'bg-blood',
  muted: 'bg-ink3',
}

export function Chip({
  tone = 'muted',
  dot = false,
  pulse = false,
  children,
}: {
  tone?: Tone
  dot?: boolean
  pulse?: boolean
  children: ReactNode
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-[2px] text-[11px] leading-[1.6] font-medium whitespace-nowrap ${CHIP_TONE[tone]}`}
    >
      {dot && (
        <span
          aria-hidden="true"
          className={`h-[5px] w-[5px] rounded-full ${CHIP_DOT[tone]}`}
          style={pulse ? { animation: 'pulse-dot 1.4s ease-in-out infinite' } : undefined}
        />
      )}
      {children}
    </span>
  )
}

/* ============================================================
   CALLOUT
   A tinted panel with a left rule and the icon that matches its
   tone, so the severity is legible before the sentence is read.
   ============================================================ */

const CALLOUT_TONE: Record<Tone, { box: string; icon: string }> = {
  ok: { box: 'border-moss/30 bg-moss-soft', icon: 'text-moss' },
  warn: { box: 'border-amber/35 bg-amber-soft', icon: 'text-amber' },
  bad: { box: 'border-blood/30 bg-blood-soft', icon: 'text-blood' },
  muted: { box: 'border-rule-strong bg-sunk', icon: 'text-ink3' },
}

const CALLOUT_ICON: Record<Tone, typeof IconInfo> = {
  ok: IconCheck,
  warn: IconAlert,
  bad: IconAlert,
  muted: IconInfo,
}

export function Callout({
  tone = 'muted',
  title,
  children,
  actions,
}: {
  tone?: Tone
  title?: string
  children?: ReactNode
  actions?: ReactNode
}) {
  const meta = CALLOUT_TONE[tone]
  const Icon = CALLOUT_ICON[tone]
  return (
    <div className={`flex gap-3 rounded-lg border px-3.5 py-3 ${meta.box}`}>
      <Icon size={17} className={`mt-[1px] ${meta.icon}`} />
      <div className="min-w-0 flex-1 text-[13px] leading-[1.55] text-ink">
        {title && <div className="mb-0.5 text-[13.5px] font-semibold">{title}</div>}
        {children}
        {actions && <div className="mt-2.5 flex flex-wrap gap-2">{actions}</div>}
      </div>
    </div>
  )
}

/* ============================================================
   ERROR
   Says what went wrong, then what to do about it.
   ============================================================ */

export function ErrorNote({
  error,
  onRetry,
}: {
  error: { message: string; offline?: boolean; status?: number } | null
  onRetry?: () => void
}) {
  if (!error) return null
  return (
    <Callout
      tone="bad"
      title={error.offline ? 'The API is not responding' : 'That request failed'}
      actions={
        onRetry ? (
          <button type="button" className="btn btn-sm" onClick={onRetry}>
            Try again
          </button>
        ) : undefined
      }
    >
      <p>{error.message}</p>
    </Callout>
  )
}

/* ============================================================
   STAT
   One measured figure with its name. Mono and large, because these
   are the numbers the whole screen exists to report.
   ============================================================ */

export function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'accent' | 'blood' | 'amber'
}) {
  const colour =
    tone === 'accent'
      ? 'text-accent'
      : tone === 'blood'
        ? 'text-blood'
        : tone === 'amber'
          ? 'text-amber'
          : 'text-ink'
  return (
    <div className="min-w-0">
      <p className="eyebrow truncate">{label}</p>
      <p className={`mt-1 font-mono text-[24px] leading-none tabular-nums ${colour}`}>{value}</p>
      {sub && <p className="mt-1.5 text-[11.5px] text-ink3">{sub}</p>}
    </div>
  )
}

/* ============================================================
   EMPTY STATE
   ============================================================ */

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="card flex flex-col items-center px-6 py-12 text-center">
      {icon && (
        <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl border border-rule bg-sunk text-accent">
          {icon}
        </div>
      )}
      <h3 className="text-[17px] font-semibold">{title}</h3>
      <div className="mx-auto mt-1.5 max-w-[58ch] text-[13px] leading-[1.6] text-ink2">
        {children}
      </div>
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

/* ============================================================
   DISCLOSURE CHEVRON
   ============================================================ */

export function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      aria-hidden="true"
      className="shrink-0 text-ink3"
      style={{
        transform: open ? 'rotate(90deg)' : 'none',
        transition: 'transform 200ms cubic-bezier(.22,1,.36,1)',
      }}
    >
      <path d="M3 1l5 4-5 4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/* ============================================================
   BUSY BAR
   An indeterminate sweep for work whose duration is genuinely unknown.
   Used only while a request is in flight, never as decoration.
   ============================================================ */

export function BusyBar() {
  return (
    <div className="h-[3px] w-full overflow-hidden bg-sunk" role="presentation">
      <div
        className="h-full w-1/3 rounded-full"
        style={{
          background: 'linear-gradient(90deg, transparent, var(--c-accent-bright), transparent)',
          animation: 'sweep 1.2s ease-in-out infinite',
        }}
      />
    </div>
  )
}

/* ============================================================
   SKELETON
   Shown only on a first load, where the shape of what is coming is
   known. Never as a stand-in for an error.
   ============================================================ */

export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="card overflow-hidden" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 border-b border-rule px-4 py-3.5 last:border-b-0">
          <div className="skeleton h-3 w-[26%]" />
          <div className="skeleton h-3 w-[14%]" />
          <div className="skeleton ml-auto h-3 w-[18%]" />
        </div>
      ))}
    </div>
  )
}
