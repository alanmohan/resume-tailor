import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { FileText, SearchX } from 'lucide-react'
import { EmptyState, ErrorAlert, LoadingBlock } from '@/components/app'
import { Button } from '@/components/ui/button'
import { getJob, listJobs } from '@/lib/api'
import { isApiError } from '@/lib/errors'
import { useProfile, useSessionStatus } from '@/lib/hooks'
import { queryKeys } from '@/lib/queryKeys'
import type { Profile } from '@/lib/types'
import { JobForm } from './JobForm'
import { JobReview } from './JobReview'
import { JOB_ID_PARAM, NEW_JOB_PARAM, NEW_JOB_PATH } from './jobRoutes'
import { newestJob } from './jobSummary'

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
        <>
          <Button asChild>
            <Link to="/job">Open my latest job</Link>
          </Button>
          <Button asChild variant="outline">
            <Link to={NEW_JOB_PATH}>Analyze a new job</Link>
          </Button>
        </>
      }
    />
  )
}

interface WithProfile {
  profile: Profile
}

/** Loads one job and hands it to the review screen. */
function LoadedJob({ jobId, profile }: WithProfile & { jobId: string }) {
  const query = useQuery({ queryKey: queryKeys.job(jobId), queryFn: () => getJob(jobId) })

  // Keep the editor mounted whenever a job is loaded, even if a later
  // background refresh fails, so unsaved edits are never thrown away.
  // The key gives every job its own draft state.
  if (query.data) return <JobReview key={query.data.job_id} job={query.data} profile={profile} />
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
 * Decides from the URL what to show (see jobRoutes.ts): the job named in the
 * URL, otherwise the newest job of this session so that a refresh keeps the
 * user's place, otherwise the empty form.
 */
function TargetJob({ profile }: WithProfile) {
  const [params] = useSearchParams()

  const jobsQuery = useQuery({ queryKey: queryKeys.jobs, queryFn: listJobs })
  const jobs = jobsQuery.data?.jobs ?? []

  if (params.has(NEW_JOB_PARAM)) return <JobForm profile={profile} earlierJobs={jobs} />

  const jobId = params.get(JOB_ID_PARAM) ?? newestJob(jobs)?.job_id
  if (jobId) return <LoadedJob jobId={jobId} profile={profile} />
  if (jobsQuery.isPending) return <LoadingBlock label="Loading your target job..." lines={4} />
  if (jobsQuery.isError) {
    return (
      <ErrorAlert
        error={jobsQuery.error}
        title="Your jobs could not be loaded"
        onRetry={() => void jobsQuery.refetch()}
        isRetrying={jobsQuery.isFetching}
      />
    )
  }
  return <JobForm profile={profile} earlierJobs={jobs} />
}

/** Route "/job": needs a session and a profile; the profile need not be confirmed yet. */
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
