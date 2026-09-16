import { useEffect, useRef, useState } from 'react'
import { ApiError, getJob, type JobStatus } from '../api'
import { isTerminal } from './format'

const POLL_MS = 2000
const RETRY_MS = 4000

/**
 * Polls one job until it reaches a terminal state, then stops.
 *
 * THREE THINGS THIS HAS TO GET RIGHT
 *
 * 1. It stops. `ready` and `failed` are terminal and no further request is
 *    scheduled. A leaked poll loop hammers the API forever and is invisible
 *    from the browser, so the only symptom is a server log nobody reads.
 * 2. It never overlaps. A setTimeout chained after each response, rather than
 *    setInterval, means a slow response cannot stack up a queue of requests.
 * 3. It tears down. The cleanup cancels the pending timer and marks the run
 *    dead, so a late response from an unmounted view cannot set state.
 */
export function useJobPoll(jobId: string | null) {
  const [job, setJob] = useState<JobStatus | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  // Mounting with a job id means polling starts immediately, so the initial
  // value has to reflect that rather than flash "not polling" for one render.
  const [polling, setPolling] = useState(Boolean(jobId))
  const timer = useRef<number | undefined>(undefined)

  // Clearing the previous job's status when the id changes is a render-time
  // adjustment, not a side effect. Done during render, the stale job never
  // reaches the screen; done in an effect, it would paint once under the wrong
  // id and then correct itself.
  const [seenJobId, setSeenJobId] = useState(jobId)
  if (jobId !== seenJobId) {
    setSeenJobId(jobId)
    setJob(null)
    setError(null)
    setPolling(Boolean(jobId))
  }

  useEffect(() => {
    if (!jobId) return

    let dead = false

    const stop = () => {
      if (!dead) setPolling(false)
    }

    const tick = async () => {
      try {
        const next = await getJob(jobId)
        if (dead) return
        setJob(next)
        setError(null)
        if (isTerminal(next.state)) {
          stop()
          return // terminal: do not reschedule
        }
        timer.current = window.setTimeout(tick, POLL_MS)
      } catch (err) {
        if (dead) return
        const apiErr = err as ApiError
        setError(apiErr)
        // A 404 means the job is genuinely gone. The registry is in memory, so
        // a server restart loses every job. Retrying cannot bring it back.
        if (apiErr.status === 404) {
          stop()
          return
        }
        // Anything else is probably transient, so back off and keep trying.
        timer.current = window.setTimeout(tick, RETRY_MS)
      }
    }

    void tick()

    return () => {
      dead = true
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [jobId])

  return { job, error, polling }
}
