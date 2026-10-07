import { describe, expect, it } from 'vitest'
import {
  activeRequirements,
  draftFromJob,
  hasErrors,
  isDirty,
  isUserEdited,
  newRequirement,
  requirementPositions,
  toJobPatchRequest,
  validateDraft,
  type JobDraft,
} from '../requirementDraft'
import { makeJob, makeRequirement } from './jobFixtures'

const MAX = 25

function draftOf(overrides: Partial<JobDraft> = {}): JobDraft {
  return { ...draftFromJob(makeJob()), ...overrides }
}

describe('draftFromJob', () => {
  it('copies the editable fields and remembers the version it came from', () => {
    const draft = draftFromJob(makeJob({ version: 7, title: null, company: null }))

    expect(draft.version).toBe(7)
    expect(draft.title).toBe('')
    expect(draft.company).toBe('')
    expect(draft.requirements).toHaveLength(4)
    expect(draft.requirements[0]).toEqual({
      key: 'req-1',
      requirement_id: 'req-1',
      text: 'Strong Python skills',
      category: 'skill',
      importance: 'required',
      removed: false,
    })
  })
})

describe('toJobPatchRequest', () => {
  it('sends the base version, trims text and turns blank fields into null', () => {
    const draft = draftOf({ version: 4, title: '  Staff Engineer ', company: '   ' })
    draft.requirements[0].text = '  Strong Python skills  '

    const request = toJobPatchRequest(draft)

    expect(request.expected_version).toBe(4)
    expect(request.title).toBe('Staff Engineer')
    expect(request.company).toBeNull()
    expect(request.requirements[0]).toEqual({
      requirement_id: 'req-1',
      text: 'Strong Python skills',
      category: 'skill',
      importance: 'required',
    })
  })

  it('leaves out removed requirements and sends a null ID for added ones', () => {
    const draft = draftOf()
    draft.requirements[1].removed = true
    draft.requirements.push({ ...newRequirement('preferred'), text: 'Experience with Terraform' })

    const request = toJobPatchRequest(draft)

    expect(request.requirements.map((item) => item.requirement_id)).toEqual([
      'req-1',
      'req-3',
      'req-4',
      null,
    ])
    expect(activeRequirements(draft)).toHaveLength(4)
  })
})

describe('isDirty', () => {
  it('is false for an untouched draft and for whitespace-only differences', () => {
    const job = makeJob()
    const draft = draftFromJob(job)
    expect(isDirty(draft, job)).toBe(false)

    draft.title = `  ${draft.title}  `
    draft.requirements[0].text += '   '
    expect(isDirty(draft, job)).toBe(false)
  })

  it('is true for a changed text, category, importance, removal or addition', () => {
    const job = makeJob()
    const changes: ((draft: JobDraft) => void)[] = [
      (draft) => (draft.requirements[0].text = 'Something else'),
      (draft) => (draft.requirements[0].category = 'experience'),
      (draft) => (draft.requirements[0].importance = 'preferred'),
      (draft) => (draft.requirements[0].removed = true),
      (draft) => draft.requirements.push(newRequirement('required')),
      (draft) => (draft.company = 'Another company'),
    ]
    for (const change of changes) {
      const draft = draftFromJob(job)
      change(draft)
      expect(isDirty(draft, job)).toBe(true)
    }
  })

  it('is true when the stored job has moved on to a newer version', () => {
    const job = makeJob({ version: 1 })
    const draft = draftFromJob(job)

    expect(isDirty(draft, { ...job, version: 2 })).toBe(true)
  })
})

describe('validateDraft', () => {
  it('accepts the job as extracted', () => {
    const errors = validateDraft(draftOf(), MAX)

    expect(errors).toEqual({ requirements: {} })
    expect(hasErrors(errors)).toBe(false)
  })

  it('reports blank and over-long requirement text by requirement key', () => {
    const draft = draftOf()
    draft.requirements[0].text = '   '
    draft.requirements[1].text = 'x'.repeat(501)

    const errors = validateDraft(draft, MAX)

    expect(errors.requirements['req-1']).toBe('Describe the requirement, or remove it.')
    expect(errors.requirements['req-2']).toMatch(/500 characters or fewer \(it has 501\)/)
    expect(hasErrors(errors)).toBe(true)
  })

  it('ignores requirements that are marked for removal', () => {
    const draft = draftOf()
    draft.requirements[0].text = ''
    draft.requirements[0].removed = true

    expect(hasErrors(validateDraft(draft, MAX))).toBe(false)
  })

  it('reports too many requirements, counting only the ones that are kept', () => {
    const draft = draftOf()
    expect(validateDraft(draft, 3).count).toBe(
      'A job can have at most 3 requirements. Remove 1 to save.',
    )

    draft.requirements[3].removed = true
    expect(validateDraft(draft, 3).count).toBeUndefined()
  })

  it('reports a role title or company name that is too long', () => {
    const errors = validateDraft(draftOf({ title: 't'.repeat(201), company: 'c'.repeat(201) }), MAX)

    expect(errors.title).toMatch(/role title to 200 characters/)
    expect(errors.company).toMatch(/company name to 200 characters/)
  })
})

describe('requirementPositions', () => {
  it('numbers required requirements first, then preferred ones', () => {
    const draft = draftFromJob(
      makeJob({
        requirements: [
          makeRequirement({ requirement_id: 'a', importance: 'preferred' }),
          makeRequirement({ requirement_id: 'b', importance: 'required' }),
          makeRequirement({ requirement_id: 'c', importance: 'preferred' }),
          makeRequirement({ requirement_id: 'd', importance: 'required' }),
        ],
      }),
    )

    const positions = requirementPositions(draft.requirements)

    expect([...positions.entries()]).toEqual([
      ['b', 1],
      ['d', 2],
      ['a', 3],
      ['c', 4],
    ])
  })
})

describe('isUserEdited', () => {
  it('is true when the server says so or text, category or importance were changed', () => {
    const stored = makeRequirement({ text: 'Strong Python skills' })
    const draft = draftFromJob(makeJob({ requirements: [stored] })).requirements[0]

    expect(isUserEdited(draft, stored)).toBe(false)
    expect(isUserEdited({ ...draft, text: 'Strong Python skills  ' }, stored)).toBe(false)
    expect(isUserEdited({ ...draft, text: 'Expert Python skills' }, stored)).toBe(true)
    expect(isUserEdited({ ...draft, category: 'experience' }, stored)).toBe(true)
    expect(isUserEdited({ ...draft, importance: 'preferred' }, stored)).toBe(true)
    expect(isUserEdited(draft, { ...stored, user_edited: true })).toBe(true)
  })
})

describe('newRequirement', () => {
  it('creates distinct unsaved rows in the requested group', () => {
    const first = newRequirement('preferred')
    const second = newRequirement('preferred')

    expect(first.requirement_id).toBeNull()
    expect(first.importance).toBe('preferred')
    expect(first.text).toBe('')
    expect(first.key).not.toBe(second.key)
  })
})
