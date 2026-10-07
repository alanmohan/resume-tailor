import { z } from 'zod'
import type { JobCreateRequest } from '@/lib/types'

/** Longest role title or company name the API accepts. */
export const MAX_JOB_FIELD_CHARS = 200

export interface JobFormValues {
  title: string
  company: string
  description: string
}

export const EMPTY_JOB_FORM: JobFormValues = { title: '', company: '', description: '' }

/** The form fields the API can report an error on, by request-body path. */
export function isJobFormField(path: string): path is keyof JobFormValues {
  return path === 'title' || path === 'company' || path === 'description'
}

function shortText(fieldName: string) {
  return z
    .string()
    .refine(
      (value) => value.trim().length <= MAX_JOB_FIELD_CHARS,
      `Keep the ${fieldName} to ${MAX_JOB_FIELD_CHARS} characters or fewer.`,
    )
}

/**
 * Validation for the job form. The description limit comes from the server's
 * session limits. `length` counts UTF-16 units, which is never less than the
 * server's count, so the client can only be stricter.
 */
export function buildJobSchema(maxJobChars: number) {
  const limit = new Intl.NumberFormat('en-US').format(maxJobChars)
  return z.object({
    title: shortText('role title'),
    company: shortText('company name'),
    description: z
      .string()
      .refine(
        (text) => text.trim() !== '',
        'Paste the job description so its requirements can be extracted.',
      )
      .refine(
        (text) => text.length <= maxJobChars,
        `The job description is longer than the ${limit} character limit. Remove parts that do not describe the role, such as benefits or company background.`,
      ),
  })
}

/** Trimmed text, or null when nothing is left (the API's "no value"). */
export function textOrNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

/**
 * The description is sent exactly as pasted: the server reports each
 * requirement's supporting span as offsets into that text.
 */
export function toJobCreateRequest(values: JobFormValues): JobCreateRequest {
  return {
    description: values.description,
    title: textOrNull(values.title),
    company: textOrNull(values.company),
  }
}
