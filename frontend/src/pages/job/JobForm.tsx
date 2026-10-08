import { useMemo, useState } from 'react'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'
import { useForm, useWatch } from 'react-hook-form'
import { CharCounter, ErrorAlert, PageHeader } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Field, FieldDescription, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { createJob } from '@/lib/api'
import { fieldErrorMap, isApiError } from '@/lib/errors'
import { isProfileReady, useProviderMode, useSession } from '@/lib/hooks'
import { queryKeys } from '@/lib/queryKeys'
import type { Job, Profile } from '@/lib/types'
import { SAMPLE_JOB } from '@/sample/sampleData'
import { DraftList } from './DraftList'
import {
  EMPTY_JOB_FORM,
  buildJobSchema,
  isJobFormField,
  toJobCreateRequest,
  type JobFormValues,
} from './jobFormSchema'
import { ProfileNotReadyAlert } from './ProfileNotReadyAlert'
import { ANALYZE_STEP_LABEL, GENERATE_STEP_LABEL, RunProgress, type RunStep } from './RunProgress'

interface JobFormProps {
  profile: Profile
  /** Step 1 is done: the job is stored. The page goes on to step 2 with it. */
  onAnalyzed: (job: Job) => void
}

const optionalHint = <span className="font-normal text-muted-foreground">(optional)</span>

/**
 * Paste a job description and tailor the resume to it in one go. This form
 * runs step 1 (the job analysis) and hands the stored job to the page, which
 * runs step 2 (writing the documents).
 */
export function JobForm({ profile, onAnalyzed }: JobFormProps) {
  const { limits } = useSession()
  const providerMode = useProviderMode()
  const queryClient = useQueryClient()
  const [sampleLoaded, setSampleLoaded] = useState(false)
  const profileReady = isProfileReady(profile)

  const resolver = useMemo(
    () => zodResolver(buildJobSchema(limits.max_job_chars)),
    [limits.max_job_chars],
  )
  const form = useForm<JobFormValues>({ defaultValues: EMPTY_JOB_FORM, resolver })
  const description = useWatch({ control: form.control, name: 'description' })
  const { errors } = form.formState

  const analyze = useMutation({
    mutationFn: (values: JobFormValues) => createJob(toJobCreateRequest(values)),
    // The next screens read the job from the cache instead of asking for it again.
    onSuccess: (job) => queryClient.setQueryData(queryKeys.job(job.job_id), job),
    onError: (error) => {
      // Put the API's field errors next to the fields they belong to.
      for (const [path, message] of Object.entries(fieldErrorMap(error))) {
        if (isJobFormField(path)) form.setError(path, { type: 'server', message })
      }
    },
  })

  const submit = form.handleSubmit((values) => {
    // Pressing Enter in a field submits the form even while the button is disabled.
    if (!profileReady || analyze.isPending) return
    // onSuccess runs only while this form is still on screen, so a user who
    // moved on during the analysis does not start a generation by surprise.
    analyze.mutate(values, { onSuccess: onAnalyzed })
  })

  // Shown from the first submission on. A finished analysis leaves this form.
  const steps: RunStep[] =
    analyze.isPending || analyze.isError
      ? [
          { label: ANALYZE_STEP_LABEL, status: analyze.isPending ? 'running' : 'failed' },
          { label: GENERATE_STEP_LABEL, status: 'waiting' },
        ]
      : []

  function fillSample() {
    // reset() replaces all three values and clears earlier validation messages.
    form.reset({
      title: SAMPLE_JOB.title,
      company: SAMPLE_JOB.company,
      description: SAMPLE_JOB.description,
    })
    setSampleLoaded(true)
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <PageHeader
        title="Target job"
        description={
          <p>
            Paste the posting you want to apply for. Its requirements are extracted, then your
            resume and cover letter are written for it from your confirmed profile.
          </p>
        }
      />

      {profileReady ? null : <ProfileNotReadyAlert profile={profile} />}

      <form onSubmit={(event) => void submit(event)} noValidate className="space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-xl font-medium">The job you are applying for</h2>
            <p className="text-sm text-muted-foreground">
              The role title and company are used to address the cover letter.
            </p>
          </div>
          <Button type="button" variant="outline" onClick={fillSample} disabled={analyze.isPending}>
            <Sparkles aria-hidden="true" />
            Use sample job
          </Button>
        </div>
        <p role="status" aria-live="polite" className="text-sm text-muted-foreground empty:hidden">
          {sampleLoaded ? 'Fictional sample job loaded. The company in it does not exist.' : ''}
        </p>

        <fieldset disabled={analyze.isPending} className="min-w-0 space-y-5">
          <legend className="sr-only">Job details</legend>
          <div className="grid gap-5 sm:grid-cols-2">
            <Field data-invalid={errors.title ? true : undefined}>
              <FieldLabel htmlFor="job-title">Role title {optionalHint}</FieldLabel>
              <Input
                id="job-title"
                autoComplete="off"
                aria-invalid={errors.title ? true : undefined}
                aria-describedby={errors.title ? 'job-title-error' : undefined}
                {...form.register('title')}
              />
              <FieldError id="job-title-error" errors={[errors.title]} />
            </Field>
            <Field data-invalid={errors.company ? true : undefined}>
              <FieldLabel htmlFor="job-company">Company {optionalHint}</FieldLabel>
              <Input
                id="job-company"
                autoComplete="off"
                aria-invalid={errors.company ? true : undefined}
                aria-describedby={errors.company ? 'job-company-error' : undefined}
                {...form.register('company')}
              />
              <FieldError id="job-company-error" errors={[errors.company]} />
            </Field>
          </div>

          <Field data-invalid={errors.description ? true : undefined}>
            <FieldLabel htmlFor="job-description">Job description</FieldLabel>
            <Textarea
              id="job-description"
              rows={12}
              placeholder="Paste the full text of the job posting"
              className="max-h-[28rem] min-h-48"
              aria-required="true"
              aria-invalid={errors.description ? true : undefined}
              aria-describedby={
                errors.description
                  ? 'job-description-error job-description-count job-description-note'
                  : 'job-description-count job-description-note'
              }
              {...form.register('description')}
            />
            <CharCounter
              id="job-description-count"
              count={description.length}
              max={limits.max_job_chars}
            />
            <FieldError id="job-description-error" errors={[errors.description]} />
            <FieldDescription id="job-description-note">
              {providerMode === 'fake'
                ? 'This server runs in demo mode: the job description is analyzed by a built-in stand-in, not sent to an AI provider.'
                : 'The job description is sent to the configured AI provider (OpenAI) to extract its requirements.'}{' '}
              It is stored with your session for at most {limits.session_ttl_hours} hours.
            </FieldDescription>
          </Field>
        </fieldset>

        <RunProgress steps={steps} />

        {analyze.isError ? (
          <ErrorAlert
            error={analyze.error}
            title="The job description was not analyzed"
            // Retrying cannot help once the session's analysis quota is used up.
            onRetry={isApiError(analyze.error, 'quota_exceeded') ? undefined : () => void submit()}
          />
        ) : null}

        <Button
          type="submit"
          size="lg"
          disabled={analyze.isPending || !profileReady}
          aria-describedby={profileReady ? undefined : 'tailor-blocked'}
        >
          {analyze.isPending ? 'Tailoring...' : 'Tailor my resume'}
        </Button>
        {profileReady ? null : (
          <p id="tailor-blocked" className="text-sm text-muted-foreground">
            Tailoring is unavailable until your profile is confirmed and indexed.
          </p>
        )}
      </form>

      <DraftList />
    </div>
  )
}
