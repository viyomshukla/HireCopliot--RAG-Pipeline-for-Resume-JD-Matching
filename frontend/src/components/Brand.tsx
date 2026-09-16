/**
 * THE MARK
 *
 * Three bars of decreasing length - a ranking - with the top one checked off.
 * That is the whole product in one glyph: an ordered list, and a claim that
 * something was verified rather than guessed.
 *
 * Drawn with `currentColor` for the bars and a token for the plate, so it
 * survives both themes and can be dropped into a dark sidebar or a light
 * header without a second asset.
 */

export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      focusable="false"
      className="shrink-0"
    >
      <rect width="32" height="32" rx="8" fill="var(--c-accent)" />
      <rect
        x="0.5"
        y="0.5"
        width="31"
        height="31"
        rx="7.5"
        stroke="var(--c-accent-bright)"
        strokeOpacity="0.45"
      />
      <g stroke="var(--c-accent-ink)" strokeWidth="2.1" strokeLinecap="round">
        <path d="M8 11h9" />
        <path d="M8 16h13" />
        <path d="M8 21h6" />
      </g>
      <circle cx="22.5" cy="10.5" r="4.4" fill="var(--c-accent-ink)" />
      <path
        d="m20.6 10.5 1.5 1.5 2.8-3"
        stroke="var(--c-accent)"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className="flex items-center gap-2.5 min-w-0">
      <Mark size={compact ? 24 : 30} />
      <span className="min-w-0 leading-none">
        <span className="block truncate text-[15.5px] font-semibold tracking-[-0.02em] text-ink">
          HireMind
        </span>
        {!compact && (
          <span className="mt-[3px] block truncate text-[11px] text-ink3">
            Evidence-first screening
          </span>
        )}
      </span>
    </span>
  )
}
