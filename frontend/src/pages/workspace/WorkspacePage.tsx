import { Link, useParams } from 'react-router'
import { CircleAlert, FileSearch, FileText } from 'lucide-react'
import { EmptyState, ErrorAlert, LoadingBlock, PageHeader } from '@/components/app'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { isApiError } from '@/lib/errors'
import { useSessionStatus } from '@/lib/hooks'
import type { Generation } from '@/lib/types'
import { jobPath } from '@/pages/job/jobRoutes'
import { useGeneration } from './generationData'
import { Workspace } from './Workspace'
import { jobLabel } from './workspaceModel'

const PAGE_TITLE = 'Your tailored draft'

/** `to` is the draft's own job when one is known; the bare "/job" opens the newest job. */
function BackToJobLink({ label, to = '/job' }: { label: string; to?: string }) {
  return (
    // text-foreground: inside the red failure alert the link should still look like a normal button.
    <Button asChild variant="outline" className="text-foreground">
      <Link to={to}>{label}</Link>
    </Button>
  )
}

/** There is no session in this tab, so there is nothing this page could load. */
function NoSession() {
  return (
    <EmptyState
      icon={FileText}
      title="No draft to show"
      description="Drafts belong to the browser tab that created them. Start by adding your profile, then generate a draft for a target job."
      action={
        <Button asChild>
          <Link to="/">Go to Start</Link>
        </Button>
      }
    />
  )
}

/** The ID is unknown, expired or belongs to another session; the API answers 404 for all three. */
function DraftNotFound() {
  return (
    <EmptyState
      icon={FileSearch}
      title="Draft not found"
      description="This draft does not exist in your session. It may have expired, been cleared, or the link may belong to a different browser tab."
      action={<BackToJobLink label="Go to target job" />}
    />
  )
}

/** Shown while the server works on the draft. The page re-reads it every few seconds. */
function GenerationRunning({ generation }: { generation: Generation }) {
  const target = jobLabel(generation)
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title={PAGE_TITLE}
        description={target ? <p>Tailoring your resume and cover letter for {target}.</p> : undefined}
      />
      <div className="space-y-4 rounded-xl border p-5">
        <LoadingBlock label="Generating..." lines={4} />
        <p className="text-sm text-muted-foreground">
          The server is retrieving evidence, writing both documents and validating every
          statement. This can take a minute or two; the page updates by itself.
        </p>
      </div>
      <BackToJobLink label="Back to target job" to={jobPath(generation.job_id)} />
    </div>
  )
}

/** The server gave up on this draft. Nothing is shown as generated; the way forward is a new attempt. */
function GenerationFailed({ generation }: { generation: Generation }) {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title={PAGE_TITLE} />
      <Alert variant="destructive">
        <CircleAlert aria-hidden="true" />
        <AlertTitle>This draft could not be generated</AlertTitle>
        <AlertDescription>
          <p className="break-words">
            {generation.error?.message ?? 'The server did not report a reason.'}
          </p>
          <p>Nothing was generated. Your profile and the target job are unchanged.</p>
        </AlertDescription>
        {/* Outside AlertDescription, which styles every link inside it as underlined text. */}
        <div className="col-start-2 mt-2">
          <BackToJobLink label="Back to target job" to={jobPath(generation.job_id)} />
        </div>
      </Alert>
    </div>
  )
}

/** Placeholder in the shape of the two-pane screen while the draft is first loaded. */
function WorkspaceSkeleton() {
  return (
    <div className="space-y-6">
      <LoadingBlock label="Loading your draft..." lines={0} />
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_23rem]">
        <div className="space-y-4 rounded-sm border bg-card px-5 py-7 sm:px-10 sm:py-10">
          <Skeleton className="h-8 w-1/2" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="mt-6 h-4 w-full" />
          <Skeleton className="h-4 w-11/12" />
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="mt-6 h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
        </div>
        <div className="hidden space-y-3 rounded-xl border bg-card p-4 lg:block">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-10 w-1/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
        </div>
      </div>
    </div>
  )
}

/**
 * Route "/workspace/:generationId": loads one draft and shows the state it
 * is in. An expired session never reaches this component; the app shell
 * replaces the page with its own "session has ended" screen.
 */
export default function WorkspacePage() {
  const { generationId = '' } = useParams()
  const sessionStatus = useSessionStatus()
  const query = useGeneration(generationId)

  if (sessionStatus !== 'active') return <NoSession />

  const generation = query.data
  if (generation) {
    if (generation.status === 'running') return <GenerationRunning generation={generation} />
    if (generation.status === 'failed') return <GenerationFailed generation={generation} />
    // Keyed by ID so tab, selection and form state never carry over to another draft.
    return <Workspace key={generation.generation_id} generation={generation} />
  }
  if (isApiError(query.error, 'not_found')) return <DraftNotFound />
  if (query.isError) {
    return (
      <ErrorAlert
        error={query.error}
        title="This draft could not be loaded"
        onRetry={() => void query.refetch()}
        isRetrying={query.isFetching}
      />
    )
  }
  return <WorkspaceSkeleton />
}
