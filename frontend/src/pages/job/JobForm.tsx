import { useMemo, useState } from 'react'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'
import { useForm, useWatch } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { CharCounter, ErrorAlert, LoadingBlock, PageHeader } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Field, FieldDescription, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { createJob } from '@/lib/api'
import { fieldErrorMap, isApiError } from '@/lib/errors'
import { isProfileReady, useProviderMode, useSession } from '@/lib/hooks'
import { queryKeys } from '@/lib/queryKeys'
import type { JobSummary, Profile } from '@/lib/types'
import { SAMPLE_JOB } from '@/sample/sampleData'
import { EarlierJobs } from './EarlierJobs'
import {
  EMPTY_JOB_FORM,
  buildJobSchema,
  isJobFormField,
  toJobCreateRequest,
  type JobFormValues,
} from './jobFormSchema'
import { jobPath } from './jobRoutes'
import { ProfileNotReadyAlert } from './ProfileNotReadyAlert'

interface JobFormProps {
  profile: Profile
  /** Jobs analyzed earlier in this session, offered below the form. */
  earlierJobs: JobSummary[]
}

const optionalHint = <span className="font-normal text-muted-foreground">(optional)</span>

/** Paste a job description and have its requirements extracted. */
export function JobForm({ profile, earlierJobs }: JobFormProps) {
  const { limits } = useSession()
  const providerMode = useProviderMode()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [sampleLoaded, setSampleLoaded] = useState(false)

  const resolver = useMemo(
    () => zodResolver(buildJobSchema(limits.max_job_chars)),
    [limits.max_job_chars],
  )
  const form = useForm<JobFormValues>({ defaultValues: EMPTY_JOB_FORM, resolver })
  const description = useWatch({ control: form.control, name: 'description' })
  const { errors } = form.formState

  const analyze = useMutation({
    mutationFn: (values: JobFormValues) => createJob(toJobCreateRequest(values)),
    onSuccess: (job) => {
      queryClient.setQueryData(queryKeys.job(job.job_id), job)
      // Only the list is out of date; the job itself was just stored above.
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs, exact: true })
    },
    onError: (error) => {
      // Put the API's field errors next to the fields they belong to.
      for (const [path, message] of Object.entries(fieldErrorMap(error))) {
        if (isJobFormField(path)) form.setError(path, { type: 'server', message })
      }
    },
  })

  const submit = form.handleSubmit((values) =>
    analyze.mutate(values, {
      // Runs only while this form is still on screen, so a user who moved on
      // during the analysis is not pulled back here.
      onSuccess: (job) => {
        toast.success('Job description analyzed')
        void navigate(jobPath(job.job_id), { replace: true })
      },
    }),
  )

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
            Paste the posting you want to apply for. Its requirements are extracted for you to
            review and correct before any document is generated.
          </p>
        }
      />

      {isProfileReady(profile) ? null : <ProfileNotReadyAlert profile={profile} />}

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

        {analyze.isError ? (
          <ErrorAlert
            error={analyze.error}
            title="The job description was not analyzed"
            // Retrying cannot help once the session's analysis quota is used up.
            onRetry={isApiError(analyze.error, 'quota_exceeded') ? undefined : () => void submit()}
          />
        ) : null}

        {analyze.isPending ? <LoadingBlock label="Analyzing job description..." lines={2} /> : null}

        <Button type="submit" size="lg" disabled={analyze.isPending}>
          {analyze.isPending ? 'Analyzing...' : 'Analyze job'}
        </Button>
      </form>

      <EarlierJobs jobs={earlierJobs} />
    </div>
  )
}
