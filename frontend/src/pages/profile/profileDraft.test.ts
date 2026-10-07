import { describe, expect, it } from 'vitest'
import { makeBullet, makeProfile, makeRecord } from '@/test/fixtures'
import {
  draftFromProfile,
  indexProgressLabel,
  isDirty,
  newBullet,
  newRecord,
  recordFieldsChanged,
  toPatchRequest,
  validateDraft,
} from './profileDraft'

describe('profile draft', () => {
  it('round-trips an unchanged profile without marking it dirty', () => {
    const profile = makeProfile()
    const draft = draftFromProfile(profile)

    expect(isDirty(draft, profile)).toBe(false)
    const patch = toPatchRequest(profile.version, draft)
    expect(patch.expected_version).toBe(1)
    expect(patch.contact).toEqual(profile.contact)
    expect(patch.records.map((record) => record.record_id)).toEqual(['rec-1', 'rec-2', 'rec-3'])
    expect(patch.records[0].bullets).toEqual([
      { bullet_id: 'bul-1', text: 'Built a search service in Python' },
    ])
    expect(patch).not.toHaveProperty('conflict_resolutions')
  })

  it('keeps dates exactly as written and turns blank fields into null', () => {
    const profile = makeProfile({
      records: [makeRecord({ start_date: 'Summer 2022', end_date: "Dec '23", location: null })],
    })
    const draft = draftFromProfile(profile)
    draft.records[0].organization = '   '

    const [record] = toPatchRequest(1, draft).records

    expect(record.start_date).toBe('Summer 2022')
    expect(record.end_date).toBe("Dec '23")
    expect(record.location).toBeNull()
    expect(record.organization).toBeNull()
  })

  it('sends new items with null ids and drops blank bullets and removed records', () => {
    const profile = makeProfile({
      records: [makeRecord(), makeRecord({ record_id: 'rec-9', title: 'Old role' })],
    })
    const draft = draftFromProfile(profile)
    draft.records[0].bullets.push({ ...newBullet(), text: 'Mentored two interns' }, newBullet())
    draft.records[1].removed = true
    draft.records.push({ ...newRecord('project'), title: 'Trail Map App' })

    const patch = toPatchRequest(1, draft)

    expect(isDirty(draft, profile)).toBe(true)
    expect(patch.records.map((record) => record.record_id)).toEqual(['rec-1', null])
    expect(patch.records[0].bullets).toEqual([
      { bullet_id: 'bul-1', text: 'Built a search service in Python' },
      { bullet_id: null, text: 'Mentored two interns' },
    ])
    expect(patch.records[1]).toMatchObject({ category: 'project', title: 'Trail Map App' })
  })

  it('includes conflict resolutions only when there are some', () => {
    const draft = draftFromProfile(makeProfile())

    const patch = toPatchRequest(3, draft, [{ conflict_id: 'con-1', resolution: 'dismissed' }])

    expect(patch.conflict_resolutions).toEqual([{ conflict_id: 'con-1', resolution: 'dismissed' }])
  })

  it('requires a title on every record that is kept', () => {
    const draft = draftFromProfile(makeProfile())
    draft.records[0].title = ' '
    draft.records[1].title = ''
    draft.records[1].removed = true

    expect(validateDraft(draft)).toEqual({ 'rec-1': 'Enter a title, or remove this record.' })
  })

  it('detects edits to a record but ignores surrounding whitespace', () => {
    const original = makeRecord({ bullets: [makeBullet()] })
    const [draft] = draftFromProfile(makeProfile({ records: [original] })).records

    expect(recordFieldsChanged({ ...draft, title: ' Software Engineer ' }, original)).toBe(false)
    expect(recordFieldsChanged({ ...draft, end_date: 'Jan 2026' }, original)).toBe(true)
  })

  it('labels indexing progress with real counts only', () => {
    expect(indexProgressLabel(makeProfile({ index_progress: { total: 0, embedded: 0 } }))).toBe(
      'Building evidence records...',
    )
    expect(indexProgressLabel(makeProfile({ index_progress: { total: 34, embedded: 12 } }))).toBe(
      'Embedding 12 of 34 evidence records',
    )
  })
})
