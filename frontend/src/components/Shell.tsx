import { useEffect, useState, type ReactNode } from 'react'
import type { JobStatus } from '../api'
import { JOB_STATE_LABEL, isTerminal } from '../lib/format'
import { useHealth, type Health } from '../lib/useHealth'
import { useTheme, type Theme } from '../lib/useTheme'
import { Wordmark } from './Brand'
import {
  IconBatches,
  IconFairness,
  IconMenu,
  IconMonitor,
  IconMoon,
  IconRank,
  IconSun,
  IconUpload,
  IconX,
} from './icons'

/**
 * THE SHELL
 *
 * A persistent frame: navigation, the batch currently being worked on, and
 * the state of the backend, all fixed in place while the views change inside
 * it. The point is orientation. Four screens that each re-declare their own
 * header leave the user reconstructing "which batch am I looking at" on every
 * screen; a shell answers it once, in the same place, permanently.
 */

export type View = 'batches' | 'upload' | 'rank' | 'fairness'

type NavItem = {
  key: View
  label: string
  hint: string
  Icon: typeof IconBatches
}

const NAV: NavItem[] = [
  { key: 'batches', label: 'Batches', hint: 'Everything uploaded to this server', Icon: IconBatches },
  { key: 'upload', label: 'Upload', hint: 'Add a zip of resumes', Icon: IconUpload },
  { key: 'rank', label: 'Rank', hint: 'Job description to shortlist', Icon: IconRank },
  { key: 'fairness', label: 'Fairness', hint: 'Who the shortlist selects', Icon: IconFairness },
]

/* ============================================================
   API STATUS
   ============================================================ */

const HEALTH_META: Record<Health, { dot: string; label: string; title: string }> = {
  checking: { dot: 'bg-ink3', label: 'Checking', title: 'Contacting the API' },
  up: { dot: 'bg-moss', label: 'API online', title: 'The backend responded' },
  down: {
    dot: 'bg-blood',
    label: 'API offline',
    title: 'The backend did not respond. Start the FastAPI server on port 8000.',
  },
}

function HealthDot({ health, jobs }: { health: Health; jobs: number | null }) {
  const meta = HEALTH_META[health]
  return (
    <div
      className="flex items-center gap-2 px-2 py-1.5"
      title={meta.title}
      role="status"
      aria-live="polite"
    >
      <span
        aria-hidden="true"
        className={`h-[7px] w-[7px] rounded-full ${meta.dot}`}
        style={health === 'checking' ? { animation: 'pulse-dot 1.2s ease-in-out infinite' } : undefined}
      />
      <span className="text-[11.5px] text-ink2">{meta.label}</span>
      {health === 'up' && jobs != null && (
        <span className="ml-auto font-mono text-[11px] text-ink3">
          {jobs} {jobs === 1 ? 'batch' : 'batches'}
        </span>
      )}
    </div>
  )
}

/* ============================================================
   THEME SWITCH
   A three-way segmented control, because `system` is a real choice
   and a two-state toggle cannot express it.
   ============================================================ */

const THEMES: [Theme, string, typeof IconSun][] = [
  ['light', 'Light', IconSun],
  ['system', 'System', IconMonitor],
  ['dark', 'Dark', IconMoon],
]

function ThemeSwitch() {
  const { theme, setTheme } = useTheme()
  return (
    <div
      className="flex rounded-md border border-rule bg-sunk p-[2px]"
      role="group"
      aria-label="Colour theme"
    >
      {THEMES.map(([value, label, Icon]) => (
        <button
          key={value}
          type="button"
          onClick={() => setTheme(value)}
          aria-pressed={theme === value}
          title={`${label} theme`}
          className={`flex flex-1 items-center justify-center rounded-[4px] py-1 transition-colors ${
            theme === value
              ? 'bg-surface text-ink shadow-[var(--shadow-xs)]'
              : 'text-ink3 hover:text-ink'
          }`}
        >
          <Icon size={14} />
          <span className="sr-only">{label}</span>
        </button>
      ))}
    </div>
  )
}

/* ============================================================
   ACTIVE BATCH
   The one piece of session context worth carrying across every
   screen: which pile of resumes is being worked on, and whether
   the server has finished with it.
   ============================================================ */

function BatchContext({
  jobId,
  job,
  onOpen,
}: {
  jobId: string | null
  job: JobStatus | null
  onOpen: () => void
}) {
  if (!jobId) {
    return (
      <div className="rounded-lg border border-dashed border-rule-strong px-3 py-2.5">
        <p className="eyebrow">Active batch</p>
        <p className="mt-1 text-[12px] text-ink3">
          None selected. Open one from Batches, or upload a zip.
        </p>
      </div>
    )
  }

  const running = job != null && !isTerminal(job.state)
  const failed = job?.state === 'failed'

  return (
    <button
      type="button"
      onClick={onOpen}
      className="w-full rounded-lg border border-rule bg-surface px-3 py-2.5 text-left transition-colors hover:border-rule-strong"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="eyebrow">Active batch</span>
        <span
          className={`text-[11px] font-medium ${
            failed ? 'text-blood' : running ? 'text-accent-mid' : 'text-moss'
          }`}
        >
          {job ? JOB_STATE_LABEL[job.state] : '...'}
        </span>
      </div>

      <p className="mt-1 truncate font-mono text-[11.5px] text-ink">{jobId}</p>

      {/* While the server is working, the shell carries the progress. This is
          the reason it is worth polling here: you can leave the Upload screen
          and still see that the batch is moving. */}
      {running && (
        <div className="mt-2">
          <div className="h-[3px] w-full overflow-hidden rounded-full bg-sunk">
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%`, transition: 'width 400ms linear' }}
            />
          </div>
          <p className="mt-1 truncate text-[11px] text-ink3">{job?.stage_message}</p>
        </div>
      )}

      {job?.state === 'ready' && (
        <p className="mt-1 text-[11px] text-ink3">
          <span className="font-mono text-ink2">{job.n_candidates}</span> candidates indexed
        </p>
      )}
    </button>
  )
}

/* ============================================================
   SIDEBAR
   ============================================================ */

function Sidebar({
  view,
  onView,
  activeJobId,
  activeJob,
  health,
  jobs,
}: {
  view: View
  onView: (v: View) => void
  activeJobId: string | null
  activeJob: JobStatus | null
  health: Health
  jobs: number | null
}) {
  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto px-3 py-4">
      <div className="px-2 pt-1">
        <Wordmark />
      </div>

      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {NAV.map(({ key, label, hint, Icon }) => {
          const current = view === key
          return (
            <button
              key={key}
              type="button"
              onClick={() => onView(key)}
              aria-current={current ? 'page' : undefined}
              title={hint}
              className={`group relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors ${
                current
                  ? 'bg-surface text-ink shadow-[var(--shadow-xs)]'
                  : 'text-ink2 hover:bg-surface/60 hover:text-ink'
              }`}
            >
              {/* The active marker is a rule against the sidebar edge rather
                  than a filled pill: it points at the screen it belongs to. */}
              <span
                aria-hidden="true"
                className={`absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-r-full transition-colors ${
                  current ? 'bg-accent' : 'bg-transparent'
                }`}
              />
              <Icon size={16} className={current ? 'text-accent' : 'text-ink3'} />
              <span className={`text-[13.5px] ${current ? 'font-semibold' : 'font-medium'}`}>
                {label}
              </span>
            </button>
          )
        })}
      </nav>

      <BatchContext
        jobId={activeJobId}
        job={activeJob}
        onOpen={() => onView(activeJob?.state === 'ready' ? 'rank' : 'upload')}
      />

      <div className="mt-auto flex flex-col gap-2 border-t border-rule pt-3">
        <HealthDot health={health} jobs={jobs} />
        <ThemeSwitch />
      </div>
    </div>
  )
}

/* ============================================================
   SHELL
   ============================================================ */

export function AppShell({
  view,
  onView,
  title,
  description,
  actions,
  activeJobId,
  activeJob,
  children,
}: {
  view: View
  onView: (v: View) => void
  title: string
  description: string
  actions?: ReactNode
  activeJobId: string | null
  activeJob: JobStatus | null
  children: ReactNode
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const { health, jobs } = useHealth()

  // The mobile drawer is a modal surface: it must not outlive the screen it
  // covers, and Escape has to close it like any other overlay.
  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menuOpen])

  const sidebar = (
    <Sidebar
      view={view}
      onView={(v) => {
        onView(v)
        setMenuOpen(false)
      }}
      activeJobId={activeJobId}
      activeJob={activeJob}
      health={health}
      jobs={jobs}
    />
  )

  return (
    <div className="min-h-screen bg-canvas lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
      {/* --- sidebar, permanent from lg up --- */}
      <aside className="sticky top-0 hidden h-screen border-r border-rule bg-paper lg:block">
        {sidebar}
      </aside>

      {/* --- sidebar as a drawer below lg --- */}
      {menuOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setMenuOpen(false)}
            className="absolute inset-0 bg-ink/40"
          />
          <div className="absolute inset-y-0 left-0 w-[268px] border-r border-rule bg-paper shadow-[var(--shadow-lg)] animate-rise">
            {sidebar}
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 border-b border-rule bg-paper/90 backdrop-blur">
          <div className="flex items-center gap-3 px-4 py-3 sm:px-6">
            <button
              type="button"
              className="btn btn-icon btn-ghost lg:hidden"
              aria-label="Open navigation"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              {menuOpen ? <IconX size={18} /> : <IconMenu size={18} />}
            </button>

            <div className="min-w-0 flex-1">
              <h1 className="truncate text-[17px] leading-tight font-semibold">{title}</h1>
              <p className="mt-0.5 truncate text-[12.5px] text-ink2">{description}</p>
            </div>

            {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
          </div>

          {/* The tool's whole claim, stated where it cannot be missed and in
              the same place on every screen. */}
          <p className="flex items-center gap-2 border-t border-rule bg-sunk/70 px-4 py-1.5 text-[11.5px] text-ink2 sm:px-6">
            <span aria-hidden="true" className="h-1 w-1 rounded-full bg-accent" />
            This tool ranks candidates and shows its evidence. It does not decide who to hire.
          </p>
        </header>

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:py-8">{children}</main>
      </div>
    </div>
  )
}
