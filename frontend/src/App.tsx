import { useState } from 'react'
import type { AuditResponse, RankResponse } from './api'
import { AppShell, type View } from './components/Shell'
import { IconUpload } from './components/icons'
import { useJobPoll } from './lib/useJobPoll'
import { BatchesView } from './views/BatchesView'
import { FairnessView } from './views/FairnessView'
import { RankView } from './views/RankView'
import { UploadView } from './views/UploadView'

/**
 * Client-side view switching, no router.
 *
 * Four views, no deep links, and the session state below is deliberately not
 * in the URL. A router would add a dependency and a set of shareable URLs that
 * this app specifically should not have.
 */

const PAGE: Record<View, { title: string; description: string }> = {
  batches: {
    title: 'Batches',
    description: 'Every pile of resumes this server has processed.',
  },
  upload: {
    title: 'Upload',
    description: 'Send one zip of resumes and watch the server read them.',
  },
  rank: {
    title: 'Rank',
    description: 'A job description becomes a rubric, and the rubric becomes a shortlist.',
  },
  fairness: {
    title: 'Fairness audit',
    description: 'Who the shortlist selects, compared across groups the ranker never sees.',
  },
}

export default function App() {
  const [view, setView] = useState<View>('batches')

  /*
    COMPANY NAME: REACT STATE ONLY, ON PURPOSE.

    Never sent to the backend, never written to localStorage, never put in the
    URL. It is a label for this session and it is meant to disappear on
    refresh. See the longer note in views/UploadView.tsx.

    This is a privacy decision, not a missing feature. Persisting it would
    attach "who was hiring" to a stored pile of resumes, and the backend has
    no field for it because it should not have one.
  */
  const [company, setCompany] = useState('')

  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [jdText, setJdText] = useState('')
  const [shortlistSize, setShortlistSize] = useState(45)
  const [rankResult, setRankResult] = useState<RankResponse | null>(null)
  const [auditResult, setAuditResult] = useState<AuditResponse | null>(null)

  /* The single poller for the active batch.
     It lives here, above the views, for two reasons: the shell shows the
     batch's progress on every screen, and one poller at this level is one
     request every two seconds instead of one per mounted view. */
  const { job: activeJob, error: pollError, polling } = useJobPoll(activeJobId)

  // A different batch invalidates the ranking computed from the old one, so the
  // two are cleared together with the job rather than in an effect watching it.
  // Doing it here keeps one batch's candidates from ever appearing under
  // another batch's id, even for a single render.
  const selectJob = (jobId: string | null) => {
    setActiveJobId(jobId)
    setRankResult(null)
    setAuditResult(null)
  }

  const page = PAGE[view]
  const trimmedCompany = company.trim()

  return (
    <AppShell
      view={view}
      onView={setView}
      title={page.title}
      // The company name is a label for this session, so it belongs in the
      // subtitle of whatever screen you are on rather than replacing the
      // heading of every one of them.
      description={
        trimmedCompany ? `${page.description} Hiring for ${trimmedCompany}.` : page.description
      }
      actions={
        view === 'batches' ? (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              selectJob(null)
              setView('upload')
            }}
          >
            <IconUpload size={15} />
            Upload resumes
          </button>
        ) : undefined
      }
      activeJobId={activeJobId}
      activeJob={activeJob}
    >
      {view === 'batches' && (
        <BatchesView
          activeJobId={activeJobId}
          onOpen={(jobId) => {
            selectJob(jobId)
            setView('rank')
          }}
          onUpload={() => {
            selectJob(null)
            setView('upload')
          }}
        />
      )}

      {view === 'upload' && (
        <UploadView
          company={company}
          onCompany={setCompany}
          activeJobId={activeJobId}
          job={activeJob}
          pollError={pollError}
          polling={polling}
          onJobStarted={selectJob}
          onRank={() => setView('rank')}
        />
      )}

      {view === 'rank' && (
        <RankView
          jobId={activeJobId}
          jdText={jdText}
          onJdText={setJdText}
          shortlistSize={shortlistSize}
          onShortlistSize={setShortlistSize}
          result={rankResult}
          onResult={setRankResult}
          onAudit={() => setView('fairness')}
          onPickBatch={() => setView('batches')}
        />
      )}

      {view === 'fairness' && (
        <FairnessView
          jobId={activeJobId}
          jdText={jdText}
          shortlistSize={shortlistSize}
          result={auditResult}
          onResult={setAuditResult}
          onRank={() => setView('rank')}
        />
      )}
    </AppShell>
  )
}
