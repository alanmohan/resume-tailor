import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { Job } from '@/lib/types'

const numberFormat = new Intl.NumberFormat('en-US')

interface JobDetailsProps {
  job: Job
  /** Draft values of the two editable fields. */
  title: string
  company: string
  titleError: string | undefined
  companyError: string | undefined
  disabled: boolean
  onChange: (change: { title?: string; company?: string }) => void
}

/**
 * The job's editable role title and company, the summary produced by the
 * analysis, and the original posting to compare the requirements against.
 * Summary and posting are untrusted text and are rendered as plain text only.
 */
export function JobDetails({
  job,
  title,
  company,
  titleError,
  companyError,
  disabled,
  onChange,
}: JobDetailsProps) {
  return (
    <section aria-labelledby="job-details-heading" className="space-y-4">
      <h2 id="job-details-heading" className="text-xl font-medium">
        Job details
      </h2>

      <fieldset disabled={disabled} className="grid min-w-0 gap-4 sm:grid-cols-2">
        <legend className="sr-only">Role and company</legend>
        <div className="space-y-1.5">
          <Label htmlFor="review-title">Role title</Label>
          <Input
            id="review-title"
            autoComplete="off"
            value={title}
            aria-invalid={titleError ? true : undefined}
            aria-describedby={titleError ? 'review-title-error' : undefined}
            onChange={(event) => onChange({ title: event.target.value })}
          />
          {titleError ? (
            <p id="review-title-error" role="alert" className="text-sm text-destructive">
              {titleError}
            </p>
          ) : null}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="review-company">Company</Label>
          <Input
            id="review-company"
            autoComplete="off"
            value={company}
            aria-invalid={companyError ? true : undefined}
            aria-describedby={companyError ? 'review-company-error' : undefined}
            onChange={(event) => onChange({ company: event.target.value })}
          />
          {companyError ? (
            <p id="review-company-error" role="alert" className="text-sm text-destructive">
              {companyError}
            </p>
          ) : null}
        </div>
      </fieldset>

      {job.role_summary ? (
        <div className="space-y-1">
          <p className="text-sm font-medium">Role summary, generated from the job description</p>
          <p className="text-sm break-words whitespace-pre-wrap text-muted-foreground">
            {job.role_summary}
          </p>
        </div>
      ) : null}

      <Accordion type="single" collapsible className="rounded-xl border px-4">
        <AccordionItem value="description">
          <AccordionTrigger>
            Job description as pasted ({numberFormat.format(job.description.length)} characters)
          </AccordionTrigger>
          <AccordionContent>
            {/* Scrolls inside itself, so it is focusable for keyboard users. */}
            <div
              role="region"
              aria-label="Job description text"
              tabIndex={0}
              className="max-h-96 overflow-y-auto rounded-md font-serif text-[0.95rem] leading-relaxed break-words whitespace-pre-wrap outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {job.description}
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </section>
  )
}
