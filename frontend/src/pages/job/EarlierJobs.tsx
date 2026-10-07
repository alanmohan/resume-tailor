import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import type { JobSummary } from '@/lib/types'
import { jobPath } from './jobRoutes'
import { formatDateTime, jobDisplayName } from './jobSummary'

/** Jobs already analyzed in this session, so starting another one is never a dead end. */
export function EarlierJobs({ jobs }: { jobs: JobSummary[] }) {
  if (jobs.length === 0) return null
  return (
    <section aria-labelledby="earlier-jobs-heading" className="space-y-3">
      <h2 id="earlier-jobs-heading" className="text-xl font-medium">
        Jobs you already analyzed
      </h2>
      <ul className="divide-y rounded-xl border">
        {jobs.map((job) => {
          const name = jobDisplayName(job)
          return (
            <li
              key={job.job_id}
              className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3"
            >
              <div className="min-w-0 space-y-0.5">
                <p className="font-medium break-words">{name}</p>
                <p className="text-xs text-muted-foreground">
                  {job.requirement_count}{' '}
                  {job.requirement_count === 1 ? 'requirement' : 'requirements'}, analyzed{' '}
                  {formatDateTime(job.created_at)}
                </p>
              </div>
              <Button asChild variant="outline" size="sm">
                <Link to={jobPath(job.job_id)}>
                  Review <span className="sr-only">{name}</span>
                </Link>
              </Button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
