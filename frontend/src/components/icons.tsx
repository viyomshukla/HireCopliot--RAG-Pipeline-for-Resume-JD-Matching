import type { SVGProps } from 'react'

/**
 * ONE ICON SYSTEM, DRAWN NOT IMPORTED
 *
 * A 24x24 grid, 1.7 stroke, round caps, `currentColor` throughout, so every
 * icon inherits the colour of the text it sits beside and follows the theme
 * without a second thought. Kept local rather than pulled from a package: the
 * app needs about eighteen glyphs, and an icon dependency would ship a
 * thousand plus a build step to shake them out again.
 *
 * Every icon is `aria-hidden`. They sit next to a label or inside a control
 * that already carries an accessible name; announcing them twice is noise.
 */

type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Icon({ size = 16, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className="shrink-0"
      {...rest}
    >
      {children}
    </svg>
  )
}

/* --- navigation ------------------------------------------------------- */

export const IconBatches = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3 3 7.5l9 4.5 9-4.5L12 3Z" />
    <path d="m3 12.5 9 4.5 9-4.5" />
    <path d="m3 17 9 4.5 9-4.5" />
  </Icon>
)

export const IconUpload = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 16V4" />
    <path d="m7.5 8.5 4.5-4.5 4.5 4.5" />
    <path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" />
  </Icon>
)

export const IconRank = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9 6h11" />
    <path d="M9 12h11" />
    <path d="M9 18h11" />
    <path d="M4 5.5 5.5 5v3.5" />
    <path d="M4 11.2c.5-.7 2-.7 2 .4 0 .9-2 1.4-2 2.4h2.2" />
    <path d="M4 16.5h2l-1.2 1.4A1.1 1.1 0 1 1 4 19.6" />
  </Icon>
)

export const IconFairness = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3.5v17" />
    <path d="M7 20.5h10" />
    <path d="M5 7.5h14" />
    <path d="m5 7.5-2.5 6a2.8 2.8 0 0 0 5 0Z" />
    <path d="m19 7.5-2.5 6a2.8 2.8 0 0 0 5 0Z" />
  </Icon>
)

/* --- status ----------------------------------------------------------- */

export const IconCheck = (p: IconProps) => (
  <Icon {...p}>
    <path d="m4.5 12.5 5 5 10-11" />
  </Icon>
)

export const IconX = (p: IconProps) => (
  <Icon {...p}>
    <path d="m6 6 12 12M18 6 6 18" />
  </Icon>
)

export const IconAlert = (p: IconProps) => (
  <Icon {...p}>
    <path d="M10.6 4.2 2.9 17.5A1.6 1.6 0 0 0 4.3 20h15.4a1.6 1.6 0 0 0 1.4-2.5L13.4 4.2a1.6 1.6 0 0 0-2.8 0Z" />
    <path d="M12 9.5v4" />
    <path d="M12 17h.01" />
  </Icon>
)

export const IconInfo = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5" />
    <path d="M12 8h.01" />
  </Icon>
)

export const IconShield = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3.2 5 6v5.5c0 4.2 2.9 7.6 7 9.3 4.1-1.7 7-5.1 7-9.3V6l-7-2.8Z" />
    <path d="m9.2 12 2 2 3.6-3.8" />
  </Icon>
)

/* --- objects ---------------------------------------------------------- */

export const IconFile = (p: IconProps) => (
  <Icon {...p}>
    <path d="M13.5 3H7a1.8 1.8 0 0 0-1.8 1.8v14.4A1.8 1.8 0 0 0 7 21h10a1.8 1.8 0 0 0 1.8-1.8V8.3L13.5 3Z" />
    <path d="M13.3 3.2v4.4a1 1 0 0 0 1 1h4.3" />
  </Icon>
)

export const IconArchive = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3" y="4" width="18" height="4.5" rx="1.2" />
    <path d="M4.8 8.5v10A1.5 1.5 0 0 0 6.3 20h11.4a1.5 1.5 0 0 0 1.5-1.5v-10" />
    <path d="M10 12.2h4" />
  </Icon>
)

export const IconTrash = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4.5 6.5h15" />
    <path d="M9.5 6.5V5a1.2 1.2 0 0 1 1.2-1.2h2.6A1.2 1.2 0 0 1 14.5 5v1.5" />
    <path d="M6.5 6.5 7.4 19a1.5 1.5 0 0 0 1.5 1.4h6.2a1.5 1.5 0 0 0 1.5-1.4l.9-12.5" />
    <path d="M10.5 10.5v6M13.5 10.5v6" />
  </Icon>
)

export const IconClock = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.2V12l3 2" />
  </Icon>
)

export const IconQuote = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9.5 6.5C6.9 7.7 5.5 9.9 5.5 13v4.5h5V13H8c0-1.9.6-3.2 2.3-4.1Z" />
    <path d="M18.5 6.5c-2.6 1.2-4 3.4-4 6.5v4.5h5V13H17c0-1.9.6-3.2 2.3-4.1Z" />
  </Icon>
)

export const IconDatabase = (p: IconProps) => (
  <Icon {...p}>
    <ellipse cx="12" cy="6" rx="7.5" ry="3" />
    <path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6" />
    <path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3" />
  </Icon>
)

/* --- controls --------------------------------------------------------- */

export const IconChevronRight = (p: IconProps) => (
  <Icon {...p}>
    <path d="m9 5 7 7-7 7" />
  </Icon>
)

export const IconArrowRight = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4.5 12h15" />
    <path d="m13.5 6 6 6-6 6" />
  </Icon>
)

export const IconRefresh = (p: IconProps) => (
  <Icon {...p}>
    <path d="M20 11.5a8 8 0 0 0-13.7-5L3.5 9.2" />
    <path d="M3.5 4.5v4.7h4.7" />
    <path d="M4 12.5a8 8 0 0 0 13.7 5l2.8-2.7" />
    <path d="M20.5 19.5v-4.7h-4.7" />
  </Icon>
)

export const IconMenu = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Icon>
)

export const IconSun = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="4.2" />
    <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4" />
  </Icon>
)

export const IconMoon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M20 14.2A8.4 8.4 0 0 1 9.8 4a8.5 8.5 0 1 0 10.2 10.2Z" />
  </Icon>
)

export const IconMonitor = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3" y="4.5" width="18" height="12" rx="1.6" />
    <path d="M9 20.5h6M12 16.5v4" />
  </Icon>
)

export const IconSearch = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="10.8" cy="10.8" r="6.3" />
    <path d="m15.5 15.5 4 4" />
  </Icon>
)

export const IconSort = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 4.5v15" />
    <path d="m3.5 16 3.5 3.5L10.5 16" />
    <path d="M13.5 7h7M13.5 12h5M13.5 17h3" />
  </Icon>
)
