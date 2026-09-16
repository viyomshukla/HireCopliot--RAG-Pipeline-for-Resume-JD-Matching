import type { FilterSpec, ParsedRequirement } from '../api'
import { degree as degreeLabel } from '../lib/format'
import { IconAlert, IconCheck, IconSort } from './icons'

/**
 * THE RUBRIC THE SYSTEM INFERRED
 *
 * This is the screen where a wrong inference gets caught. A hard requirement
 * removes people before anyone is scored, so a mis-read line in the job
 * description quietly deletes qualified candidates and nothing downstream ever
 * shows it. Hard and soft are therefore separated by consequence, not styled as
 * two flavours of the same list.
 */

function Constraint({ req }: { req: ParsedRequirement }) {
  const parts: string[] = []
  if (req.min_years != null) parts.push(`at least ${req.min_years} years of experience`)
  if (req.degree_level) parts.push(`a ${degreeLabel(req.degree_level).toLowerCase()} or higher`)
  if (req.skill) parts.push(`${req.skill} recorded as a skill`)
  if (!parts.length) return null
  return <span className="text-ink3"> Enforced as {parts.join(', ')}.</span>
}

function RequirementRow({ req, hard }: { req: ParsedRequirement; hard: boolean }) {
  return (
    <li className="flex items-start gap-3 border-t border-rule py-2.5 first:border-t-0">
      <span
        className={`mt-[2px] shrink-0 rounded border px-1.5 py-[1px] font-mono text-[10.5px] ${
          hard ? 'border-blood/30 bg-blood-soft text-blood' : 'border-rule bg-sunk text-ink3'
        }`}
        title={`Weight ${req.weight}`}
      >
        w{req.weight}
      </span>
      <span className="text-[13px] leading-[1.5]">
        {req.text}
        <Constraint req={req} />
      </span>
    </li>
  )
}

/** The filter_spec is the hard requirements collapsed into the query that
 *  actually runs against the database. Showing it spelled out means the
 *  recruiter checks the filter, not a paraphrase of it. */
export function FilterSpecStatement({ spec }: { spec: FilterSpec }) {
  const clauses: string[] = []
  if (spec.min_years != null) clauses.push(`have at least ${spec.min_years} years of experience`)
  if (spec.min_degree) clauses.push(`hold a ${degreeLabel(spec.min_degree).toLowerCase()} or higher`)
  if (spec.required_skills?.length) {
    clauses.push(
      `list ${spec.required_skills.length === 1 ? 'the skill' : 'every skill'} ${spec.required_skills.join(', ')}`,
    )
  }

  // When nothing is filtered, nothing is at stake, and the warning colouring
  // would be claiming a consequence that does not exist.
  const empty = clauses.length === 0

  return (
    <div
      className={`flex gap-3 rounded-lg border px-4 py-3.5 ${
        empty ? 'border-rule-strong bg-sunk' : 'border-blood/30 bg-blood-soft'
      }`}
    >
      {empty ? (
        <IconCheck size={17} className="mt-[1px] text-ink3" />
      ) : (
        <IconAlert size={17} className="mt-[1px] text-blood" />
      )}
      <div className="min-w-0">
        <h4 className={`text-[13.5px] font-semibold ${empty ? 'text-ink' : 'text-blood'}`}>
          {empty ? 'No filter will run' : 'The filter that will run'}
        </h4>
        {!empty ? (
          <>
            <p className="mt-1 text-[13px] leading-[1.55] text-ink">
              A candidate is removed before scoring unless they{' '}
              {clauses.map((c, i) => (
                <span key={i}>
                  {i > 0 && (i === clauses.length - 1 ? ', and ' : ', ')}
                  <span className="font-semibold">{c}</span>
                </span>
              ))}
              .
            </p>
            <p className="mt-2 text-[12px] text-ink2">
              Anyone this removes is still listed, with the reason. If a line here is wrong, edit
              the job description and run it again.
            </p>
          </>
        ) : (
          <p className="mt-1 text-[13px] leading-[1.55] text-ink">
            No hard requirement was inferred, so nobody is removed before scoring. Every candidate
            in the batch is scored and ranked. If the job description does have a genuine must-have,
            say it plainly and run it again.
          </p>
        )}
      </div>
    </div>
  )
}

export function RequirementsReview({
  requirements,
  filterSpec,
  title,
}: {
  requirements: ParsedRequirement[]
  filterSpec: FilterSpec
  title?: string | null
}) {
  const hard = requirements.filter((r) => r.kind === 'hard')
  const soft = requirements.filter((r) => r.kind === 'soft')

  return (
    <div className="space-y-4">
      {title && (
        <p className="text-[12.5px] text-ink2">
          Read as a job description for{' '}
          <span className="text-[14px] font-semibold text-ink">{title}</span>.
        </p>
      )}

      {/* items-start so an empty column sizes to its content instead of
          stretching to match a long list beside it. */}
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="card overflow-hidden">
          <div className="card-head">
            <h3 className="flex items-center gap-2 text-[13.5px] font-semibold">
              <IconAlert size={15} className="text-blood" />
              Hard requirements
            </h3>
            <span className="font-mono text-[12px] text-ink3">{hard.length}</span>
          </div>
          {hard.length > 0 ? (
            <p className="border-b border-rule bg-blood-soft/70 px-4 py-2 text-[11.5px] text-blood">
              These eliminate candidates. Nobody who fails one is scored.
            </p>
          ) : (
            <p className="border-b border-rule bg-sunk px-4 py-2 text-[11.5px] text-ink2">
              Nothing here, so nobody is eliminated before scoring.
            </p>
          )}
          <div className="px-4 py-1">
            {hard.length ? (
              <ul>
                {hard.map((r, i) => (
                  <RequirementRow key={i} req={r} hard />
                ))}
              </ul>
            ) : (
              <p className="py-4 text-[12.5px] text-ink3">
                None were inferred. Every candidate will be scored.
              </p>
            )}
          </div>
        </div>

        <div className="card overflow-hidden">
          <div className="card-head">
            <h3 className="flex items-center gap-2 text-[13.5px] font-semibold">
              <IconSort size={15} className="text-accent" />
              Soft requirements
            </h3>
            <span className="font-mono text-[12px] text-ink3">{soft.length}</span>
          </div>
          <p className="border-b border-rule bg-sunk px-4 py-2 text-[11.5px] text-ink2">
            These move a candidate up or down the ranking. They never remove anyone.
          </p>
          <div className="px-4 py-1">
            {soft.length ? (
              <ul>
                {soft.map((r, i) => (
                  <RequirementRow key={i} req={r} hard={false} />
                ))}
              </ul>
            ) : (
              <p className="py-4 text-[12.5px] text-ink3">None were inferred.</p>
            )}
          </div>
        </div>
      </div>

      <FilterSpecStatement spec={filterSpec} />
    </div>
  )
}
