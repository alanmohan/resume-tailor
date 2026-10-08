import { useQuery } from '@tanstack/react-query'
import { Lightbulb, Quote } from 'lucide-react'
import { ErrorAlert, LoadingBlock, SourceExcerpt } from '@/components/app'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { getJob } from '@/lib/api'
import { isApiError } from '@/lib/errors'
import { queryKeys } from '@/lib/queryKeys'
import type { Requirement, RequirementCategory, RequirementImportance } from '@/lib/types'
import { useWorkspace } from './workspaceContext'

const CATEGORY_LABEL: Record<RequirementCategory, string> = {
  skill: 'Skill',
  experience: 'Experience',
  education: 'Education',
  certification: 'Certification',
  responsibility: 'Responsibility',
  other: 'Other',
}

/** In display order: required qualifications first. */
const GROUPS: { importance: RequirementImportance; title: string }[] = [
  { importance: 'required', title: 'Required' },
  { importance: 'preferred', title: 'Preferred' },
]

/** Marks a requirement that the analysis read between the lines of the posting. */
function InferredBadge() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* A button, so the explanation is also reachable with the keyboard. */}
        <Badge asChild variant="outline">
          <button type="button">
            <Lightbulb aria-hidden="true" />
            Inferred
          </button>
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        Not stated explicitly in the posting. It was inferred from the job description.
      </TooltipContent>
    </Tooltip>
  )
}

/** Opens the passage of the job description that a requirement was taken from. */
function ViewSource({ excerpt, position }: { excerpt: string; position: number }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant="link" size="xs" className="h-auto px-0">
          <Quote aria-hidden="true" />
          View source <span className="sr-only">of requirement {position}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 max-w-[calc(100vw-2rem)] p-3">
        {/* SourceExcerpt renders the untrusted job text as a plain text node. */}
        <SourceExcerpt excerpt={excerpt} label="Job description" />
      </PopoverContent>
    </Popover>
  )
}

/** One requirement, read-only. `position` is its number in the whole list, used in labels. */
function RequirementItem({ requirement, position }: { requirement: Requirement; position: number }) {
  return (
    <li className="space-y-1.5 py-3">
      {/* Untrusted job text: a text node, which React escapes. */}
      <p className="text-sm wrap-anywhere">{requirement.text}</p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
        <span>{CATEGORY_LABEL[requirement.category]}</span>
        {requirement.inferred ? <InferredBadge /> : null}
        {requirement.source_span ? (
          <ViewSource excerpt={requirement.source_span.excerpt} position={position} />
        ) : null}
      </div>
    </li>
  )
}

/**
 * The requirements extracted from the draft's job description, read-only and
 * grouped by importance. They are shown as stored on the job; there is
 * nothing to edit here. The Coverage tab says how each one was assessed.
 */
export function RequirementsPanel() {
  const { generation } = useWorkspace()
  const jobId = generation.job_id
  const query = useQuery({ queryKey: queryKeys.job(jobId), queryFn: () => getJob(jobId) })

  if (query.isPending) return <LoadingBlock label="Loading the job requirements..." lines={3} />
  if (isApiError(query.error, 'not_found')) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        The job this draft was written for is no longer available, so its requirements cannot be
        shown.
      </p>
    )
  }
  if (query.isError) {
    return (
      <ErrorAlert
        error={query.error}
        title="The job requirements could not be loaded"
        onRetry={() => void query.refetch()}
        isRetrying={query.isFetching}
      />
    )
  }

  const { requirements } = query.data
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-base font-medium">Job requirements</h2>
      </div>
      {GROUPS.map(({ importance, title }) => {
        const group = requirements.filter((requirement) => requirement.importance === importance)
        const headingId = `job-requirements-${importance}`
        return (
          <section key={importance} aria-labelledby={headingId}>
            <h3 id={headingId} className="text-sm font-medium">
              {title} ({group.length})
            </h3>
            {group.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">None.</p>
            ) : (
              <ul className="divide-y">
                {group.map((requirement) => (
                  <RequirementItem
                    key={requirement.requirement_id}
                    requirement={requirement}
                    position={requirements.indexOf(requirement) + 1}
                  />
                ))}
              </ul>
            )}
          </section>
        )
      })}
    </div>
  )
}
