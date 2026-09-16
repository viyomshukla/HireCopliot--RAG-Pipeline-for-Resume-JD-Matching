# Shortlist — frontend

React + TypeScript + Vite + Tailwind. Talks to the Hiring Copilot API at
`http://localhost:8000`. No mock data: if the API is down the UI says so.

## Running it

```
npm install
npm run dev          # serves on http://localhost:3000
```

Start the backend first:

```
cd ../backend
uvicorn api.main:app --reload --port 8000
```

## The port is not negotiable

The dev server is pinned to **port 3000** with `strictPort: true`.

The backend's CORS middleware allows exactly two origins:

```python
allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"]
```

Vite's own default is 5173, which is **not** on that list. Served from 5173 every
request fails as an opaque `Failed to fetch` with nothing useful in the console.
`strictPort` makes the dev server refuse to start rather than quietly fall back
to 3001 and break the API the same way.

If you change the port here, change `allow_origins` to match, or nothing works.

## Types come from the running server

`src/api-types.ts` is generated, not written by hand:

```
npx openapi-typescript http://localhost:8000/openapi.json -o src/api-types.ts
```

Regenerate after any backend change. A changed response shape then shows up as a
TypeScript error rather than as a blank panel at runtime.

Note that fields the backend declares with a default (`evidence`,
`exclusion_reasons`, `excluded`, `files`) arrive typed as optional, because
FastAPI marks them not-required in the schema. That is accurate, and the
components default them rather than asserting them away.

## One endpoint in the brief does not exist

The build brief specifies `POST /api/parse-jd`, returning the inferred
requirements without ranking anyone, to back the confirmation step.

**The backend does not expose it.** `GET /openapi.json` lists six paths:

```
/api/health  /api/upload  /api/jobs  /api/jobs/{job_id}  /api/rank  /api/audit
```

The backend was not to be modified, so the requirements-review step is built on
the rubric that `POST /api/rank` already returns alongside the results:
`requirements` and `filter_spec`. The flow still reaches the rubric before it
reaches any person. After ranking returns, the app lands on step 2 and shows
nothing about any individual until the recruiter confirms the requirements.

What this costs: the scoring has already run by the time the rubric is reviewed,
so confirming does not save the compute. What it preserves is the thing that
matters, which is that a recruiter reads the inferred hard requirements before
acting on a ranked list of people, and can go back and edit the job description
if one is wrong.

If `/api/parse-jd` is ever added, `src/views/RankView.tsx` is the only file that
needs to change: call it from step 1 and move the `rank()` call to the confirm
button in step 2.

## The company name is deliberately not persisted

`company` lives in React state in `src/App.tsx` and nowhere else. It is never
sent to the backend, never written to `localStorage`, and never put in the URL.
It disappears on refresh, by design.

This is a privacy decision. Storing "who was hiring" next to a batch of resumes
turns a pile of CVs into an identifiable record of who applied where. The
backend has no field for it and should not acquire one.

There are comments saying so in `src/App.tsx` and `src/views/UploadView.tsx`,
because the obvious "fix" is to persist it.

## Layout of the source

```
src/
  api.ts            every network call, the one base URL, error shaping
  api-types.ts      generated from the live OpenAPI schema
  App.tsx           view switching and session state
  lib/
    format.ts       labels for job states, degrees, per-file outcomes
    useJobPoll.ts   polls one job, stops on ready/failed, tears down cleanly
  components/
    Chrome.tsx      score mark, chips, callouts, section headings
    Evidence.tsx    the evidence list, split by source
    Candidates.tsx  the ranked table and the excluded list
    Requirements.tsx the inferred rubric and the filter it produces
    FileReport.tsx  per-file outcomes grouped by status
  views/
    BatchesView.tsx  UploadView.tsx  RankView.tsx  FairnessView.tsx
```

## Design notes

Two typefaces with distinct jobs. **Newsreader** carries the candidate's own
words: evidence quotes, names, stage messages. **IBM Plex Sans**, with the Mono
cut for figures and identifiers, carries the interface. The evidence quote is
the largest text in any row, because it is the thing being judged.

Density is deliberate. Rows are tight so roughly twenty candidates fit on a
screen; a recruiter comparing 45 people should not have to scroll six times.

One motion, tied to a click: the evidence drawer opening, animated with
`grid-template-rows` so it expands to its real height. Disabled under
`prefers-reduced-motion`.
