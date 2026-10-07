import { CircleHelp } from 'lucide-react'
import { StatusBadge } from '@/components/app'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { CoverageSummary, Generation } from '@/lib/types'
import { RequirementRow } from './RequirementRow'
import { useWorkspace } from './workspaceContext'
import { countLabel, coveragePercentLabel, sectionLabel } from './workspaceModel'

/** The published formula, with this draft's own numbers filled in. */
function FormulaHelp({ summary }: { summary: CoverageSummary }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant="link" size="xs" className="h-auto px-0">
          <CircleHelp aria-hidden="true" />
          How this is calculated
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 max-w-[calc(100vw-2rem)] p-3">
        <p className="font-medium">
          Evidence coverage = 100 × (supported + 0.5 × partially supported) ÷ assessed requirements
        </p>
        <p>
          Assessed requirements are those marked supported, partially supported or no evidence
          found. Uncertain requirements are left out and counted separately. Every requirement has
          the same weight.
        </p>
        {summary.percent === null ? (
          <p>This draft has no assessed requirements, so the result is unavailable rather than 0%.</p>
        ) : (
          <p>
            For this draft: 100 × ({summary.supported} + 0.5 × {summary.partial}) ÷{' '}
            {summary.assessed} = {coveragePercentLabel(summary)}
          </p>
        )}
      </PopoverContent>
    </Popover>
  )
}

/**
 * The headline number with the counts it is made of. The percentage and the
 * counts are the server's own figures; nothing is recomputed in the browser.
 */
function CoverageSummaryBlock({ summary }: { summary: CoverageSummary }) {
  return (
    <section aria-labelledby="coverage-heading" className="space-y-3">
      <div>
        <h2 id="coverage-heading" className="text-base font-medium">
          Evidence coverage
        </h2>
        <p className="mt-1 font-serif text-3xl leading-tight font-medium tabular-nums">
          {coveragePercentLabel(summary)}
        </p>
        <p className="text-sm text-muted-foreground">
          {summary.percent === null
            ? 'No requirement could be assessed, so there is no percentage to show.'
            : `Across ${countLabel(summary.assessed, 'assessed requirement')}.`}
        </p>
      </div>

      <ul aria-label="Requirement counts" className="flex flex-wrap gap-1.5">
        <li>
          <StatusBadge status="supported" label={`${summary.supported} supported`} />
        </li>
        <li>
          <StatusBadge status="partial" label={`${summary.partial} partially supported`} />
        </li>
        <li>
          <StatusBadge status="missing" label={`${summary.missing} no evidence found`} />
        </li>
        <li>
          <StatusBadge status="uncertain" label={`${summary.uncertain} uncertain not counted`} />
        </li>
      </ul>

      <FormulaHelp summary={summary} />

      <p className="text-xs text-muted-foreground">
        This reflects the evidence found in the profile you supplied. It is not a measure of job
        suitability, ATS compatibility or hiring probability.
      </p>
    </section>
  )
}

/** Statements the server left out, and its warnings about the draft, each with the reason. */
function DraftNotes({ generation }: { generation: Generation }) {
  const { omitted_claims: omitted, warnings } = generation
  if (omitted.length === 0 && warnings.length === 0) return null

  const openByDefault = [...(omitted.length > 0 ? ['omitted'] : []), ...(warnings.length > 0 ? ['warnings'] : [])]
  return (
    <Accordion type="multiple" defaultValue={openByDefault} className="border-t">
      {omitted.length > 0 ? (
        <AccordionItem value="omitted">
          <AccordionTrigger>Omitted statements ({omitted.length})</AccordionTrigger>
          <AccordionContent className="space-y-3">
            <p className="text-muted-foreground">
              These were written but left out of the documents because the evidence did not
              support them.
            </p>
            <ul className="space-y-3">
              {omitted.map((claim, index) => (
                // <div>, not <p>: the accordion adds a paragraph gap under every <p>.
                <li key={`${index}-${claim.text}`} className="space-y-1">
                  <div className="text-xs text-muted-foreground">{sectionLabel(claim.section)}</div>
                  <div className="font-serif wrap-anywhere whitespace-pre-wrap">{claim.text}</div>
                  <div className="text-muted-foreground wrap-anywhere">Reason: {claim.reason}</div>
                </li>
              ))}
            </ul>
          </AccordionContent>
        </AccordionItem>
      ) : null}
      {warnings.length > 0 ? (
        <AccordionItem value="warnings">
          <AccordionTrigger>Warnings ({warnings.length})</AccordionTrigger>
          <AccordionContent>
            <ul className="list-disc space-y-1 pl-5">
              {warnings.map((warning, index) => (
                <li key={`${index}-${warning}`} className="wrap-anywhere">
                  {warning}
                </li>
              ))}
            </ul>
          </AccordionContent>
        </AccordionItem>
      ) : null}
    </Accordion>
  )
}

/**
 * Evidence coverage for the target job: the headline figure, every
 * requirement with its status and evidence, and what the server omitted.
 */
export function CoveragePanel() {
  const { generation } = useWorkspace()
  return (
    <div className="space-y-5">
      <CoverageSummaryBlock summary={generation.coverage_summary} />

      <section aria-labelledby="requirements-heading">
        <h3 id="requirements-heading" className="text-sm font-medium">
          Requirements
        </h3>
        {generation.coverage.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">
            This job has no requirements to assess.
          </p>
        ) : (
          <ul className="divide-y">
            {generation.coverage.map((item) => (
              <RequirementRow key={item.requirement_id} item={item} />
            ))}
          </ul>
        )}
      </section>

      <DraftNotes generation={generation} />
    </div>
  )
}
