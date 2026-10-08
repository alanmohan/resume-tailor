import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import type { Job } from '@/lib/types'

const numberFormat = new Intl.NumberFormat('en-US')

/**
 * A stored job, read-only: role title, company, the summary produced by the
 * analysis and the original posting. All of it is untrusted text and is
 * rendered as plain text only.
 */
export function JobDetails({ job }: { job: Job }) {
  return (
    <section aria-labelledby="job-details-heading" className="space-y-4">
      <h2 id="job-details-heading" className="text-xl font-medium">
        Job details
      </h2>

      <dl className="grid gap-4 sm:grid-cols-2">
        <div className="min-w-0 space-y-0.5">
          <dt className="text-sm font-medium">Role title</dt>
          <dd className="break-words text-muted-foreground">{job.title ?? 'Not given'}</dd>
        </div>
        <div className="min-w-0 space-y-0.5">
          <dt className="text-sm font-medium">Company</dt>
          <dd className="break-words text-muted-foreground">{job.company ?? 'Not given'}</dd>
        </div>
      </dl>

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
