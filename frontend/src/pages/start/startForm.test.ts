import { describe, expect, it } from 'vitest'
import {
  buildStartSchema,
  emptyStartValues,
  sampleSources,
  toFormFieldName,
  toIngestPayload,
  totalCharacters,
} from './startForm'

function valuesWith(texts: [string, string, string], acknowledged = true) {
  const values = emptyStartValues()
  texts.forEach((text, index) => {
    values.sources[index].text = text
  })
  values.acknowledged = acknowledged
  return values
}

function messages(values: ReturnType<typeof emptyStartValues>, max = 60_000) {
  const result = buildStartSchema(max).safeParse(values)
  return result.success ? [] : result.error.issues.map((issue) => `${issue.path.join('.')}: ${issue.message}`)
}

describe('buildStartSchema', () => {
  it('accepts one filled source with the acknowledgement', () => {
    expect(messages(valuesWith(['My resume', '', '']))).toEqual([])
  })

  it('rejects a form with only whitespace', () => {
    expect(messages(valuesWith(['  ', '\n', '']))).toEqual([
      'sources: Paste your resume, LinkedIn profile or notes into at least one box.',
    ])
  })

  it('rejects text over the total limit and names the limit', () => {
    expect(messages(valuesWith(['a'.repeat(60), 'b'.repeat(50), '']), 100)).toEqual([
      'sources: Your text is longer than the 100 character limit. Shorten or remove some of it.',
    ])
  })

  it('requires a label only on sources that have text', () => {
    const values = valuesWith(['resume text', '', ''])
    values.sources[0].label = ' '
    values.sources[1].label = ''

    expect(messages(values)).toEqual(['sources.0.label: Give this source a label.'])
  })

  it('requires the privacy acknowledgement', () => {
    expect(messages(valuesWith(['resume text', '', ''], false))).toEqual([
      'acknowledged: Confirm that you have read how your data is handled.',
    ])
  })
})

describe('toIngestPayload', () => {
  it('drops blank sources, trims labels and leaves the text untouched', () => {
    const values = valuesWith(['', '  linkedin text\n', 'notes'])
    values.sources[1].label = '  My LinkedIn  '

    const payload = toIngestPayload(values)

    expect(payload.sources).toEqual([
      { source_type: 'linkedin', label: 'My LinkedIn', text: '  linkedin text\n' },
      { source_type: 'notes', label: 'Background notes', text: 'notes' },
    ])
    expect(payload.formIndexes).toEqual([1, 2])
  })
})

describe('toFormFieldName', () => {
  it('maps an index in the sent array back to the form slot', () => {
    expect(toFormFieldName('sources.0.text', [1, 2])).toBe('sources.1.text')
    expect(toFormFieldName('sources.1.label', [1, 2])).toBe('sources.2.label')
  })

  it('returns null for paths that are not a source field', () => {
    expect(toFormFieldName('sources', [0])).toBeNull()
    expect(toFormFieldName('sources.5.text', [0])).toBeNull()
  })
})

describe('sample and counting helpers', () => {
  it('fills every slot from the fictional sample', () => {
    const sources = sampleSources()

    expect(sources.map((source) => source.source_type)).toEqual(['resume', 'linkedin', 'notes'])
    expect(sources.every((source) => source.text.length > 0)).toBe(true)
  })

  it('counts characters across all sources', () => {
    expect(totalCharacters([{ text: 'abc' }, { text: '' }, { text: 'de' }])).toBe(5)
  })
})
