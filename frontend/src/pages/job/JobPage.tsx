import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { FileText, SearchX } from 'lucide-react'
import { EmptyState, ErrorAlert, LoadingBlock } from '@/components/app'
import { Button } from '@/components/ui/button'
import { getJob } from '@/lib/api'
import { isApiError } from '@/lib/errors'
import { useProfile, useSessionStatus } from '@/lib/hooks'
import { queryKeys } from '@/lib/queryKeys'
import type { Job, Profile } from '@/lib/types'
import { ExistingJob } from './ExistingJob'
import { JobForm } from './JobForm'
import { JOB_ID_PARAM, jobPath } from './jobRoutes'
import { useGenerateDraft, type DraftGeneration } from './useGenerateDraft'

function NoProfile() {
  return (
    <EmptyState
      icon={FileText}
      title="No profile yet"
      description="A target job is matched against your own profile. Paste your resume, LinkedIn profile or notes on the Start step first."
      action={
        <Button asChild>
          <Link to="/">Go to Start</Link>
        </Button>
      }
    />
  )
}

function JobNotFound() {
  return (
    <EmptyState
      icon={SearchX}
      title="This job is not available"
      description="It may belong to a session that has ended, or the link is incomplete."
      action={
        <Button asChild>
          <Link to="/job">Tailor for a new job</Link>
        </Button>
      }
    />
  )
}

interface LoadedJobProps {
  jobId: string
  profile: Profile
  generation: DraftGeneration
}

/** Loads the job named in the URL and shows it with "Generate a new draft". */
function LoadedJob({ jobId, profile, generation }: LoadedJobProps) {
  const query = useQuery({ queryKey: queryKeys.job(jobId), queryFn: () => getJob(jobId) })

  // Keep the job on screen once it is loaded, even if a later background
  // refresh fails, so a generation in progress keeps its progress display.
  if (query.data) return <ExistingJob job={query.data} profile={profile} generation={generation} />
  if (isApiError(query.error, 'not_found')) return <JobNotFound />
  if (query.isError) {
    return (
      <ErrorAlert
        error={query.error}
        title="The job could not be loaded"
        onRetry={() => void query.refetch()}
        isRetrying={query.isFetching}
      />
    )
  }
  return <LoadingBlock label="Loading the job..." lines={4} />
}

/**
 * Decides from the URL what to show (see jobRoutes.ts): the form, or a job
 * that is already analyzed.
 *
 * The generation state lives here, above both views, because one run crosses
 * them: the form analyzes the job (step 1), then the page moves to that job's
 * own address and writes the documents (step 2). If step 2 fails, Retry on
 * that screen repeats step 2 only, with the same idempotency key.
 */
function TargetJob({ profile }: { profile: Profile }) {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const generation = useGenerateDraft()
  const jobId = params.get(JOB_ID_PARAM)

  function handleAnalyzed(job: Job) {
    void navigate(jobPath(job.job_id), { replace: true })
    generation.start(job.job_id)
  }

  if (jobId) return <LoadedJob jobId={jobId} profile={profile} generation={generation} />
  return <JobForm profile={profile} onAnalyzed={handleAnalyzed} />
}

/** Route "/job": needs a session and a profile; tailoring also needs the profile confirmed. */
export default function JobPage() {
  const status = useSessionStatus()
  const profileQuery = useProfile()

  if (status !== 'active') return <NoProfile />
  if (profileQuery.data) return <TargetJob profile={profileQuery.data} />
  if (profileQuery.isError) {
    return (
      <ErrorAlert
        error={profileQuery.error}
        title="Your profile could not be loaded"
        onRetry={() => void profileQuery.refetch()}
        isRetrying={profileQuery.isFetching}
      />
    )
  }
  if (profileQuery.data === null) return <NoProfile />
  return <LoadingBlock label="Loading your profile..." lines={4} />
}
