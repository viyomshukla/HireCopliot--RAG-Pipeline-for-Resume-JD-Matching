import { useCallback, useEffect, useState } from 'react'
import { API_BASE } from '../api'

/**
 * IS THE BACKEND THERE?
 *
 * The old footer printed the API URL and left the reader to guess. Nearly
 * every confusing failure in this app has one cause - the FastAPI server is
 * not running - and it currently surfaces as an error inside whichever view
 * the user happened to open. A status dot in the shell answers it once,
 * before anything is clicked.
 *
 * Three states, deliberately. `checking` is not `down`: showing a red dot for
 * the 200ms before the first response lands would cry wolf on every load.
 */

export type Health = 'checking' | 'up' | 'down'

const PROBE_MS = 15000

export function useHealth() {
  const [health, setHealth] = useState<Health>('checking')
  const [jobs, setJobs] = useState<number | null>(null)

  const probe = useCallback(async (): Promise<void> => {
    try {
      const response = await fetch(`${API_BASE}/api/health`)
      if (!response.ok) throw new Error(String(response.status))
      const body = (await response.json()) as { status?: string; jobs?: number }
      setHealth('up')
      setJobs(typeof body.jobs === 'number' ? body.jobs : null)
    } catch {
      setHealth('down')
      setJobs(null)
    }
  }, [])

  useEffect(() => {
    let dead = false
    const tick = async () => {
      await probe()
      if (dead) return
    }
    void tick()
    // A slow heartbeat. This exists to catch the backend dying mid-session,
    // not to poll it at the rate of a real request.
    const id = window.setInterval(() => void tick(), PROBE_MS)
    return () => {
      dead = true
      window.clearInterval(id)
    }
  }, [probe])

  return { health, jobs, recheck: probe }
}
