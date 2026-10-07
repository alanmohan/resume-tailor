import { describe, expect, it } from 'vitest'
import {
  EMPTY_JOB_FORM,
  buildJobSchema,
  isJobFormField,
  toJobCreateRequest,
  type JobFormValues,
} from '../jobFormSchema'

function messagesFor(values: JobFormValues, maxJobChars = 25_000): Record<string, string> {
  const result = buildJobSchema(maxJobChars).safeParse(values)
  if (result.success) return {}
  return Object.fromEntries(result.error.issues.map((issue) => [issue.path.join('.'), issue.message]))
}

describe('buildJobSchema', () => {
  it('accepts a description on its own', () => {
    expect(messagesFor({ ...EMPTY_JOB_FORM, description: 'Build APIs in Python.' })).toEqual({})
  })

  it('rejects an empty or whitespace-only description', () => {
    const message = 'Paste the job description so its requirements can be extracted.'

    expect(messagesFor(EMPTY_JOB_FORM)).toEqual({ description: message })
    expect(messagesFor({ ...EMPTY_JOB_FORM, description: ' \n\t ' })).toEqual({ description: message })
  })

  it('rejects a description over the configured limit and names the limit', () => {
    const atLimit = { ...EMPTY_JOB_FORM, description: 'x'.repeat(1_500) }
    const overLimit = { ...EMPTY_JOB_FORM, description: 'x'.repeat(1_501) }

    expect(messagesFor(atLimit, 1_500)).toEqual({})
    expect(messagesFor(overLimit, 1_500).description).toMatch(
      /longer than the 1,500 character limit/,
    )
  })

  it('limits the role title and company name to 200 characters after trimming', () => {
    const valid = { title: ` ${'t'.repeat(200)} `, company: 'c'.repeat(200), description: 'Text' }
    const invalid = { title: 't'.repeat(201), company: 'c'.repeat(201), description: 'Text' }

    expect(messagesFor(valid)).toEqual({})
    expect(messagesFor(invalid)).toEqual({
      title: 'Keep the role title to 200 characters or fewer.',
      company: 'Keep the company name to 200 characters or fewer.',
    })
  })
})

describe('toJobCreateRequest', () => {
  it('sends the description untouched and trims the optional fields', () => {
    const description = '  Leading spaces matter for source offsets.\n'

    expect(toJobCreateRequest({ title: ' Engineer ', company: '', description })).toEqual({
      description,
      title: 'Engineer',
      company: null,
    })
  })
})

describe('isJobFormField', () => {
  it('recognises the three form fields and nothing else', () => {
    expect(['title', 'company', 'description'].every(isJobFormField)).toBe(true)
    expect(isJobFormField('requirements.0.text')).toBe(false)
  })
})
