import { useMemo, useState } from 'react'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { History, Sparkles } from 'lucide-react'
import { Controller, FormProvider, useForm, useWatch } from 'react-hook-form'
import { Link, useNavigate } from 'react-router'
import { toast } from 'sonner'
import { CharCounter, ErrorAlert, LoadingBlock, PageHeader } from '@/components/app'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { ingestProfile } from '@/lib/api'
import { fieldErrorMap } from '@/lib/errors'
import { useProfile, useProviderMode, useSession } from '@/lib/hooks'
import { queryKeys } from '@/lib/queryKeys'
import { ensureSession } from '@/lib/session'
import { PrivacyNotice } from './PrivacyNotice'
import { SourceField } from './SourceField'
import {
  SOURCE_SLOTS,
  buildStartSchema,
  emptyStartValues,
  sampleSources,
  toFormFieldName,
  toIngestPayload,
  totalCharacters,
  type IngestPayload,
  type StartFormValues,
} from './startForm'

/** What the submit is doing right now; each stage has an honest text label. */
type Stage = 'idle' | 'session' | 'waking' | 'extracting'

const STAGE_LABELS: Record<Exclude<Stage, 'idle'>, string> = {
  session: 'Starting a private session...',
  waking: 'Waking the server... this can take about a minute on the free hosting tier',
  extracting: 'Extracting profile... this usually takes 30 to 90 seconds',
}

export default function StartPage() {
  const { status, limits, hasProfile } = useSession()
  const providerMode = useProviderMode()
  const existingProfile = useProfile().data
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [stage, setStage] = useState<Stage>('idle')
  const [sampleLoaded, setSampleLoaded] = useState(false)

  const resolver = useMemo(
    () => zodResolver(buildStartSchema(limits.max_profile_chars)),
    [limits.max_profile_chars],
  )
  const form = useForm<StartFormValues>({ defaultValues: emptyStartValues(), resolver })
  const sources = useWatch({ control: form.control, name: 'sources' })
  const { errors } = form.formState

  const ingest = useMutation({
    mutationFn: async (payload: IngestPayload) => {
      setStage('session')
      await ensureSession({ onWaking: () => setStage('waking') })
      setStage('extracting')
      return ingestProfile(payload.sources)
    },
    onSuccess: async (profile) => {
      // Drop any profile read still in flight so it cannot overwrite the new draft.
      await queryClient.cancelQueries({ queryKey: queryKeys.profile })
      queryClient.setQueryData(queryKeys.profile, profile)
      void queryClient.invalidateQueries({ queryKey: queryKeys.session })
      void queryClient.invalidateQueries({ queryKey: queryKeys.generations })
      toast.success('Profile extracted')
      void navigate('/profile')
    },
    onError: (error, payload) => {
      // Put the API's field errors next to the fields they belong to.
      for (const [path, message] of Object.entries(fieldErrorMap(error))) {
        const name = toFormFieldName(path, payload.formIndexes)
        if (name) form.setError(name, { type: 'server', message })
      }
    },
    onSettled: () => setStage('idle'),
  })

  const submit = form.handleSubmit((values) => ingest.mutate(toIngestPayload(values)))

  function fillSample() {
    form.setValue('sources', sampleSources(), { shouldDirty: true })
    form.clearErrors('sources')
    setSampleLoaded(true)
  }

  const hasExistingProfile = status === 'active' && (hasProfile || !!existingProfile)

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <PageHeader
        title="A resume for this job, with every claim traced to your own words"
        description={
          <p>
            Paste your resume, LinkedIn profile and notes. You review the profile that is
            extracted, then get a tailored resume and cover letter that show the source of each
            claim and the job requirements no evidence was found for.
          </p>
        }
      />

      {hasExistingProfile ? (
        <Alert role="status">
          <History aria-hidden="true" />
          <AlertTitle>Continue where you left off</AlertTitle>
          <AlertDescription>
            This tab already has a profile. Extracting again below replaces it and marks earlier
            drafts as out of date.
          </AlertDescription>
          {/* Outside AlertDescription, which styles every link inside it as underlined text. */}
          <div className="col-start-2 mt-2">
            <Button asChild size="sm">
              <Link to="/profile">Open my profile</Link>
            </Button>
          </div>
        </Alert>
      ) : null}

      <PrivacyNotice providerMode={providerMode} ttlHours={limits.session_ttl_hours} />

      <FormProvider {...form}>
        <form onSubmit={(event) => void submit(event)} noValidate className="space-y-6">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="space-y-1">
              <h2 className="text-xl font-medium">Your background</h2>
              <p className="text-sm text-muted-foreground">
                Fill in at least one box. More sources give the drafts more evidence to draw on.
              </p>
            </div>
            <Button type="button" variant="outline" onClick={fillSample} disabled={ingest.isPending}>
              <Sparkles aria-hidden="true" />
              Try sample profile
            </Button>
          </div>
          <p role="status" aria-live="polite" className="text-sm text-muted-foreground empty:hidden">
            {sampleLoaded
              ? 'Fictional sample loaded. The person and employers in it do not exist.'
              : ''}
          </p>

          {SOURCE_SLOTS.map((slot, index) => (
            <SourceField key={slot.source_type} index={index} disabled={ingest.isPending} />
          ))}

          <div className="space-y-1.5">
            <CharCounter
              count={totalCharacters(sources)}
              max={limits.max_profile_chars}
              unit="characters in total"
            />
            {/* React Hook Form files an error on the array itself under "root". */}
            {errors.sources?.root?.message ? (
              <p role="alert" className="text-sm text-destructive">
                {errors.sources.root.message}
              </p>
            ) : null}
          </div>

          <Field orientation="horizontal" data-invalid={errors.acknowledged ? true : undefined}>
            <Controller
              control={form.control}
              name="acknowledged"
              render={({ field }) => (
                <Checkbox
                  id="acknowledged"
                  ref={field.ref}
                  checked={field.value}
                  onCheckedChange={(checked) => field.onChange(checked === true)}
                  onBlur={field.onBlur}
                  disabled={ingest.isPending}
                  aria-invalid={errors.acknowledged ? true : undefined}
                  aria-describedby={errors.acknowledged ? 'acknowledged-error' : undefined}
                />
              )}
            />
            <div className="space-y-1">
              <FieldLabel htmlFor="acknowledged" className="leading-snug font-normal">
                I have read how my data is sent, stored and deleted.
              </FieldLabel>
              <FieldError id="acknowledged-error" errors={[errors.acknowledged]} />
            </div>
          </Field>

          {ingest.isError ? (
            <ErrorAlert
              error={ingest.error}
              title="Your profile was not extracted"
              onRetry={() => void submit()}
            />
          ) : null}

          {ingest.isPending && stage !== 'idle' ? (
            <LoadingBlock label={STAGE_LABELS[stage]} lines={2} />
          ) : null}

          <Button type="submit" size="lg" disabled={ingest.isPending}>
            {ingest.isPending ? 'Working...' : 'Extract my profile'}
          </Button>
        </form>
      </FormProvider>
    </div>
  )
}
