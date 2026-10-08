import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { CircleX, History, LoaderCircle } from 'lucide-react'
import { ErrorAlert } from '@/components/app'
import { Badge } from '@/components/ui/badge'
import { listGenerations } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import type { GenerationSummary } from '@/lib/types'
import { workspacePath } from './jobRoutes'
import { formatDateTime, jobDisplayName } from './jobSummary'

/** Icon + text for what a draft needs the reader to know: out of date, unfinished or failed. */
function DraftBadges({ draft }: { draft: GenerationSummary }) {
  return (
    <span className="flex flex-wrap gap-1.5">
      {draft.status === 'running' ? (
        <Badge variant="outline">
          <LoaderCircle aria-hidden="true" />
          Generating
        </Badge>
      ) : null}
      {draft.status === 'failed' ? (
        <Badge variant="outline" className="border-destructive/30 bg-destructive/10 text-destructive">
          <CircleX aria-hidden="true" />
          Failed
        </Badge>
      ) : null}
      {draft.stale ? (
        <Badge variant="outline" className="border-warning/35 bg-warning/10 text-warning">
          <History aria-hidden="true" />
          Stale
        </Badge>
      ) : null}
    </span>
  )
}

/**
 * Drafts already generated in this session, newest first, each linking to its
 * workspace: those of one job when `jobId` is given, otherwise all of them,
 * each with the job it was written for.
 */
export function DraftList({ jobId }: { jobId?: string }) {
  const query = useQuery({ queryKey: queryKeys.generations, queryFn: listGenerations })
  const drafts = (query.data?.generations ?? [])
    .filter((draft) => jobId === undefined || draft.job_id === jobId)
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
  const nothingYet =
    jobId === undefined
      ? 'No draft has been generated yet.'
      : 'No draft has been generated for this job yet.'

  return (
    <section aria-labelledby="drafts-heading" className="space-y-3">
      <h2 id="drafts-heading" className="text-xl font-medium">
        {jobId === undefined ? 'Your drafts' : 'Drafts for this job'}
      </h2>
      {query.isError && !query.data ? (
        <ErrorAlert
          error={query.error}
          title="Your drafts could not be loaded"
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
        />
      ) : drafts.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {query.isPending ? 'Loading drafts...' : nothingYet}
        </p>
      ) : (
        <>
          <ul className="divide-y rounded-xl border">
            {drafts.map((draft) => (
              <li
                key={draft.generation_id}
                className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 px-4 py-3"
              >
                <div className="min-w-0">
                  <Link
                    to={workspacePath(draft.generation_id)}
                    className="font-medium text-primary underline-offset-4 hover:underline"
                  >
                    Draft from {formatDateTime(draft.created_at)}
                  </Link>
                  {jobId === undefined ? (
                    <p className="text-sm break-words text-muted-foreground">
                      {jobDisplayName({ title: draft.job_title, company: draft.company })}
                    </p>
                  ) : null}
                </div>
                <DraftBadges draft={draft} />
              </li>
            ))}
          </ul>
          {drafts.some((draft) => draft.stale) ? (
            <p className="text-sm text-muted-foreground">
              A stale draft was generated before your profile last changed. Open it and choose
              &ldquo;Generate a new draft&rdquo; to use the current version.
            </p>
          ) : null}
        </>
      )}
    </section>
  )
}
