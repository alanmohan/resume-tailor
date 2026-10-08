import { Link } from 'react-router'
import { PageHeader } from '@/components/app'
import { Button } from '@/components/ui/button'
import { isProfileReady } from '@/lib/hooks'
import type { Job, Profile } from '@/lib/types'
import { DraftList } from './DraftList'
import { GenerationError } from './GenerationError'
import { JobDetails } from './JobDetails'
import { jobDisplayName } from './jobSummary'
import { ProfileNotReadyAlert } from './ProfileNotReadyAlert'
import {
  ANALYZE_STEP_LABEL,
  GENERATE_STEP_LABEL,
  RunProgress,
  type RunStep,
  type StepStatus,
} from './RunProgress'
import type { DraftGeneration } from './useGenerateDraft'

const WAITING_NOTE = 'A draft is already being generated. Checking on it every few seconds...'

function generationStatus(generation: DraftGeneration): StepStatus {
  if (generation.isPending) return 'running'
  if (generation.error) return 'failed'
  return generation.isSuccess ? 'done' : 'waiting'
}

interface ExistingJobProps {
  /** A job that is already analyzed and stored. */
  job: Job
  profile: Profile
  /** The page's generation state; it may still be about another job. */
  generation: DraftGeneration
}

/**
 * A job that step 1 has already analyzed, shown read-only, with step 2:
 * "Generate a new draft". The combined run lands here after its analysis, and
 * the workspace links here to generate again. The job is never analyzed a
 * second time from this screen.
 */
export function ExistingJob({ job, profile, generation }: ExistingJobProps) {
  const profileReady = isProfileReady(profile)
  // Progress and errors are only shown for the job they belong to.
  const started = generation.jobId === job.job_id
  const steps: RunStep[] = started
    ? [
        { label: ANALYZE_STEP_LABEL, status: 'done' },
        { label: GENERATE_STEP_LABEL, status: generationStatus(generation) },
      ]
    : []
  const waitingForRunningDraft = started && generation.isPending && generation.phase === 'waiting'

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <PageHeader
        title="Your target job"
        description={
          <p>
            {jobDisplayName(job)}. This job description is already analyzed; generating writes a
            resume and cover letter for it from your current profile.
          </p>
        }
        actions={
          <Button asChild variant="outline">
            <Link to="/job">Start a different job</Link>
          </Button>
        }
      />

      {profileReady ? null : <ProfileNotReadyAlert profile={profile} />}

      <JobDetails job={job} />

      <RunProgress steps={steps} note={waitingForRunningDraft ? WAITING_NOTE : undefined} />

      {started && generation.error ? (
        <GenerationError error={generation.error} onRetry={() => generation.start(job.job_id)} />
      ) : null}

      <div className="space-y-2">
        <Button
          type="button"
          size="lg"
          onClick={() => generation.start(job.job_id)}
          disabled={generation.isPending || !profileReady}
          aria-describedby={profileReady ? undefined : 'generate-blocked'}
        >
          {started && generation.isPending ? 'Generating...' : 'Generate a new draft'}
        </Button>
        {profileReady ? null : (
          <p id="generate-blocked" className="text-sm text-muted-foreground">
            Generating is unavailable until your profile is confirmed and indexed.
          </p>
        )}
      </div>

      <DraftList jobId={job.job_id} />
    </div>
  )
}
