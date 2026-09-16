import { useEffect, useState } from 'react'

/**
 * THEME
 *
 * Three states, not two. `system` is the default and is a real choice: it
 * means "follow the OS", and it is what most people want. `light` and `dark`
 * are explicit overrides, written to the root element as `data-theme` so the
 * CSS in index.css can beat the `prefers-color-scheme` media query.
 *
 * Persisted to localStorage, because a theme that resets on every reload is
 * worse than no theme switch at all. This is the only thing the app stores:
 * it is a display preference and carries nothing about any candidate.
 */

export type Theme = 'light' | 'dark' | 'system'

const KEY = 'hiremind.theme'

function read(): Theme {
  try {
    const stored = localStorage.getItem(KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    /* private mode, or storage disabled. The default is a fine answer. */
  }
  return 'system'
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(read)

  // Writing an attribute on <html> is synchronising with something outside
  // React's tree, which is what an effect is for.
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem(KEY, theme)
    } catch {
      /* not being able to remember it is not worth an error */
    }
  }, [theme])

  return { theme, setTheme }
}
