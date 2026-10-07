import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { LoaderCircle } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { toast } from 'sonner'
import { CharCounter, ErrorAlert, PageHeader } from '@/components/app'
import { Button } from '@/components/ui/button'
import { updateJob } from '@/lib/api'
import { isApiError } from '@/lib/errors'
import { isProfileReady, useSession } from '@/lib/hooks'
import { queryKeys } from '@/lib/queryKeys'
import type { Job, Profile, RequirementImportance } from '@/lib/types'
import { DraftList } from './DraftList'
import { GenerationError } from './GenerationError'
import { JobDetails } from './JobDetails'
import { NEW_JOB_PATH } from './jobRoutes'
import { ProfileNotReadyAlert } from './ProfileNotReadyAlert'
import {
  activeRequirements,
  draftFromJob,
  hasErrors,
  isDirty,
  newRequirement,
  toJobPatchRequest,
  validateDraft,
  type DraftErrors,
  type DraftRequirement,
  type JobDraft,
} from './requirementDraft'
import { RequirementList, type FocusTarget } from './RequirementList'
import { useGenerateDraft, type GenerationPhase } from './useGenerateDraft'

const GENERATION_LABELS: Record<GenerationPhase, string> = {
  generating: 'Generating... this can take a minute or two',
  waiting: 'A draft is already being generated. Checking on it every few seconds...',
}

const NO_ERRORS: DraftErrors = { requirements: {} }

interface JobReviewProps {
  /** The latest copy of the job from the server. */
  job: Job
  profile: Profile
}

/**
 * Review and correct the extracted requirements, then generate.
 *
 * Edits live in a local draft until "Save changes" sends them with the job
 * version the draft was copied from (optimistic concurrency). Generation uses
 * what is stored on the server, so it is only offered when nothing is unsaved.
 */
export function JobReview({ job, profile }: JobReviewProps) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { limits } = useSession()
  const [draft, setDraft] = useState<JobDraft>(() => draftFromJob(job))
  // Validation messages appear after the first save attempt and then follow the edits.
  const [showErrors, setShowErrors] = useState(false)
  const [focusTarget, setFocusTarget] = useState<FocusTarget | null>(null)
  const generation = useGenerateDraft(job)

  const profileReady = isProfileReady(profile)
  const dirty = isDirty(draft, job)
  const errors = validateDraft(draft, limits.max_requirements)
  const shownErrors = showErrors ? errors : NO_ERRORS
  const requirementCount = activeRequirements(draft).length
  const canAddRequirement = requirementCount < limits.max_requirements

  const save = useMutation({
    mutationFn: () => updateJob(job.job_id, toJobPatchRequest(draft)),
    onSuccess: async (saved) => {
      // Drop any read still in flight so it cannot overwrite the saved job.
      await queryClient.cancelQueries({ queryKey: queryKeys.job(saved.job_id) })
      queryClient.setQueryData(queryKeys.job(saved.job_id), saved)
      setDraft(draftFromJob(saved))
      setShowErrors(false)
      // A failure reported for the previous version no longer applies.
      generation.reset()
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs, exact: true })
      // Drafts generated from the previous version are now stale.
      void queryClient.invalidateQueries({ queryKey: queryKeys.generations })
      toast.success('Changes saved')
    },
  })

  const busy = save.isPending || generation.isPending

  function handleSave() {
    if (hasErrors(errors)) {
      setShowErrors(true)
      return
    }
    save.mutate()
  }

  /** After a version conflict: load the server's copy and drop local edits. */
  async function reloadLatest() {
    const key = queryKeys.job(job.job_id)
    await queryClient.refetchQueries({ queryKey: key, exact: true })
    const latest = queryClient.getQueryData<Job>(key)
    if (latest) setDraft(draftFromJob(latest))
    setShowErrors(false)
    save.reset()
  }

  function changeRequirement(key: string, change: Partial<DraftRequirement>) {
    setDraft((current) => ({
      ...current,
      requirements: current.requirements.map((requirement) =>
        requirement.key === key ? { ...requirement, ...change } : requirement,
      ),
    }))
    // The row is drawn again in the other group; keep keyboard focus on it.
    if (change.importance) setFocusTarget({ kind: 'row', key, field: 'importance' })
  }

  function addRequirement(importance: RequirementImportance) {
    const requirement = newRequirement(importance)
    setDraft((current) => ({ ...current, requirements: [...current.requirements, requirement] }))
    setFocusTarget({ kind: 'row', key: requirement.key, field: 'text' })
  }

  /** A stored requirement is only marked, so it can be restored; an unsaved one is dropped. */
  function removeRequirement(requirement: DraftRequirement) {
    if (requirement.requirement_id === null) {
      setDraft((current) => ({
        ...current,
        requirements: current.requirements.filter((item) => item.key !== requirement.key),
      }))
      setFocusTarget({ kind: 'add', importance: requirement.importance })
    } else {
      changeRequirement(requirement.key, { removed: true })
      setFocusTarget({ kind: 'row', key: requirement.key, field: 'undo' })
    }
  }

  function restoreRequirement(key: string) {
    changeRequirement(key, { removed: false })
    setFocusTarget({ kind: 'row', key, field: 'text' })
  }

  const versionConflict = isApiError(save.error, 'version_conflict')

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <PageHeader
        title="Review the job requirements"
        description={
          <p>
            These requirements were extracted from the job description. They decide which parts
            of your profile are used as evidence and what the evidence coverage is measured
            against, so correct anything that is wrong before you generate.
          </p>
        }
        actions={
          <Button type="button" variant="outline" onClick={() => void navigate(NEW_JOB_PATH)}>
            Start a different job
          </Button>
        }
      />

      {profileReady ? null : <ProfileNotReadyAlert profile={profile} />}

      <JobDetails
        job={job}
        title={draft.title}
        company={draft.company}
        titleError={shownErrors.title}
        companyError={shownErrors.company}
        disabled={busy}
        onChange={(change) => setDraft((current) => ({ ...current, ...change }))}
      />

      <section aria-labelledby="requirements-heading" className="space-y-5">
        <div className="space-y-1.5">
          <h2 id="requirements-heading" className="text-xl font-medium">
            Requirements
          </h2>
          <p className="text-sm text-muted-foreground">
            Edit the wording, move a requirement between Required and Preferred, remove what does
            not apply, or add what the analysis missed. &ldquo;Inferred&rdquo; marks a requirement
            that is not stated explicitly in the posting.
          </p>
          <CharCounter
            count={requirementCount}
            max={limits.max_requirements}
            unit="requirements"
          />
          {shownErrors.count ? (
            <p role="alert" className="text-sm text-destructive">
              {shownErrors.count}
            </p>
          ) : canAddRequirement ? null : (
            <p className="text-sm text-muted-foreground">
              This is the most a job can have. Remove one to add another.
            </p>
          )}
        </div>

        <fieldset disabled={busy} className="min-w-0">
          <legend className="sr-only">Requirements of this job</legend>
          <RequirementList
            requirements={draft.requirements}
            stored={job.requirements}
            errors={shownErrors.requirements}
            focusTarget={focusTarget}
            canAdd={canAddRequirement}
            onAdd={addRequirement}
            onChange={changeRequirement}
            onRemove={removeRequirement}
            onRestore={restoreRequirement}
          />
        </fieldset>
      </section>

      <DraftList jobId={job.job_id} />

      {save.isError ? (
        <ErrorAlert
          error={save.error}
          title="Your changes were not saved"
          onRetry={versionConflict ? undefined : handleSave}
        >
          {versionConflict ? (
            <Button type="button" variant="outline" size="sm" onClick={() => void reloadLatest()}>
              Reload latest version (discards unsaved edits)
            </Button>
          ) : null}
        </ErrorAlert>
      ) : null}

      {generation.error ? (
        <GenerationError error={generation.error} onRetry={generation.start} />
      ) : null}

      <div className="sticky bottom-0 z-30 -mx-4 space-y-2 border-t bg-background/95 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-t-xl sm:border sm:border-b-0">
        {showErrors && hasErrors(errors) ? (
          <p role="alert" className="text-sm text-destructive">
            Some fields need attention. Fix the highlighted ones, then save again.
          </p>
        ) : null}
        <div
          id="job-action-status"
          role="status"
          aria-live="polite"
          className="text-sm text-muted-foreground"
        >
          {generation.isPending ? (
            <p className="flex items-start gap-2">
              <LoaderCircle
                aria-hidden="true"
                className="mt-0.5 size-4 shrink-0 animate-spin motion-reduce:animate-none"
              />
              {GENERATION_LABELS[generation.phase]}
            </p>
          ) : dirty ? (
            <p>You have unsaved changes. Save them before generating.</p>
          ) : profileReady ? (
            <p>All changes are saved.</p>
          ) : (
            <p>
              All changes are saved. Generating is unavailable until your profile is confirmed.{' '}
              <Link to="/profile" className="font-medium text-primary underline underline-offset-4">
                Open profile
              </Link>
            </p>
          )}
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="outline" onClick={handleSave} disabled={!dirty || busy}>
            {save.isPending ? 'Saving...' : 'Save changes'}
          </Button>
          <Button
            type="button"
            onClick={generation.start}
            disabled={busy || dirty || !profileReady}
            aria-describedby="job-action-status"
          >
            {generation.isPending ? 'Generating...' : 'Generate tailored resume and cover letter'}
          </Button>
        </div>
      </div>
    </div>
  )
}
