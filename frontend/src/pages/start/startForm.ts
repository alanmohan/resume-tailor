import { z } from 'zod'
import { SAMPLE_SOURCES } from '@/sample/sampleData'
import type { SourceInput, SourceType } from '@/lib/types'

export const MAX_LABEL_CHARS = 80

interface SourceSlot {
  source_type: SourceType
  /** Fixed heading of the paste area. */
  heading: string
  /** Initial value of the editable source label. */
  defaultLabel: string
  placeholder: string
}

/** The three paste areas, in display order. */
export const SOURCE_SLOTS: SourceSlot[] = [
  {
    source_type: 'resume',
    heading: 'Resume/CV',
    defaultLabel: 'Resume',
    placeholder: 'Paste the text of your resume or CV',
  },
  {
    source_type: 'linkedin',
    heading: 'LinkedIn profile',
    defaultLabel: 'LinkedIn profile',
    placeholder: 'Paste text copied from your LinkedIn profile page',
  },
  {
    source_type: 'notes',
    heading: 'Background notes',
    defaultLabel: 'Background notes',
    placeholder: 'Anything else: project details, results, skills you have not written up yet',
  },
]

export interface StartFormValues {
  /** One entry per slot in SOURCE_SLOTS, in the same order. */
  sources: SourceInput[]
  acknowledged: boolean
}

export function emptyStartValues(): StartFormValues {
  return {
    sources: SOURCE_SLOTS.map((slot) => ({
      source_type: slot.source_type,
      label: slot.defaultLabel,
      text: '',
    })),
    acknowledged: false,
  }
}

/** The fictional sample, placed into the matching paste areas. */
export function sampleSources(): SourceInput[] {
  return SOURCE_SLOTS.map((slot) => {
    const sample = SAMPLE_SOURCES.find((source) => source.source_type === slot.source_type)
    return {
      source_type: slot.source_type,
      label: sample?.label ?? slot.defaultLabel,
      text: sample?.text ?? '',
    }
  })
}

function isBlank(text: string): boolean {
  return text.trim() === ''
}

/**
 * Characters across all sources. `length` counts UTF-16 units, which is never
 * less than the server's count, so the client can only be stricter.
 */
export function totalCharacters(sources: Pick<SourceInput, 'text'>[]): number {
  return sources.reduce((total, source) => total + source.text.length, 0)
}

/**
 * Validation for the Start form. Cross-field problems (nothing pasted, total
 * too long) are reported on the "sources" path; label problems on the label.
 */
export function buildStartSchema(maxProfileChars: number) {
  const limit = new Intl.NumberFormat('en-US').format(maxProfileChars)
  return z
    .object({
      sources: z.array(
        z.object({
          source_type: z.enum(['resume', 'linkedin', 'notes']),
          label: z.string(),
          text: z.string(),
        }),
      ),
      acknowledged: z.boolean(),
    })
    .superRefine((values, context) => {
      if (values.sources.every((source) => isBlank(source.text))) {
        context.addIssue({
          code: 'custom',
          path: ['sources'],
          message: 'Paste your resume, LinkedIn profile or notes into at least one box.',
        })
      } else if (totalCharacters(values.sources) > maxProfileChars) {
        context.addIssue({
          code: 'custom',
          path: ['sources'],
          message: `Your text is longer than the ${limit} character limit. Shorten or remove some of it.`,
        })
      }
      values.sources.forEach((source, index) => {
        if (isBlank(source.text)) return
        const label = source.label.trim()
        if (label.length === 0) {
          context.addIssue({
            code: 'custom',
            path: ['sources', index, 'label'],
            message: 'Give this source a label.',
          })
        } else if (label.length > MAX_LABEL_CHARS) {
          context.addIssue({
            code: 'custom',
            path: ['sources', index, 'label'],
            message: `Keep the label to ${MAX_LABEL_CHARS} characters or fewer.`,
          })
        }
      })
      if (!values.acknowledged) {
        context.addIssue({
          code: 'custom',
          path: ['acknowledged'],
          message: 'Confirm that you have read how your data is handled.',
        })
      }
    })
}

export interface IngestPayload {
  sources: SourceInput[]
  /** formIndexes[i] is the form slot that became sources[i] (blank slots are not sent). */
  formIndexes: number[]
}

/** Drop blank paste areas and trim labels; the source text itself is sent untouched. */
export function toIngestPayload(values: StartFormValues): IngestPayload {
  const sources: SourceInput[] = []
  const formIndexes: number[] = []
  values.sources.forEach((source, index) => {
    if (isBlank(source.text)) return
    sources.push({ ...source, label: source.label.trim() })
    formIndexes.push(index)
  })
  return { sources, formIndexes }
}

const SOURCE_FIELD_PATH = /^sources\.(\d+)\.(label|text)$/

/**
 * Translate an API field path such as "sources.1.text" (an index into the
 * sent array) into the matching form field name, or null if it is not one.
 */
export function toFormFieldName(
  apiPath: string,
  formIndexes: number[],
): `sources.${number}.label` | `sources.${number}.text` | null {
  const match = SOURCE_FIELD_PATH.exec(apiPath)
  if (!match) return null
  const formIndex = formIndexes[Number(match[1])]
  if (formIndex === undefined) return null
  return `sources.${formIndex}.${match[2] as 'label' | 'text'}`
}
