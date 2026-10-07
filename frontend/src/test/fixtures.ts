import type { Conflict, Profile, ProfileBullet, ProfileRecord, SourceRef } from '@/lib/types'
import { futureIso } from './mockApi'

/** Fictional profile data for tests. No real person is described here. */

export function sourceRef(excerpt: string, label = 'Resume'): SourceRef {
  return { source_id: 'src-1', source_label: label, start: 0, end: excerpt.length, excerpt }
}

export function makeBullet(overrides: Partial<ProfileBullet> = {}): ProfileBullet {
  const text = overrides.text ?? 'Built a search service in Python'
  return {
    bullet_id: 'bul-1',
    text,
    source_ref: sourceRef(text),
    provenance: 'extracted',
    needs_review: false,
    review_reasons: [],
    ...overrides,
  }
}

export function makeRecord(overrides: Partial<ProfileRecord> = {}): ProfileRecord {
  return {
    record_id: 'rec-1',
    category: 'employment',
    title: 'Software Engineer',
    organization: 'Northwind Robotics',
    location: 'Columbus, OH',
    start_date: 'Aug 2022',
    end_date: 'Present',
    summary: null,
    bullets: [makeBullet()],
    skills: ['Python'],
    source_ref: sourceRef('Software Engineer - Northwind Robotics (Aug 2022 - Present)'),
    provenance: 'extracted',
    needs_review: false,
    review_reasons: [],
    ...overrides,
  }
}

export function makeConflict(overrides: Partial<Conflict> = {}): Conflict {
  return {
    conflict_id: 'con-1',
    field: 'dates',
    description: 'The resume and LinkedIn give different start dates for this role.',
    record_ids: ['rec-1'],
    values: [
      { value: 'Aug 2022', source_ref: sourceRef('Software Engineer (Aug 2022 - Present)') },
      {
        value: 'Sep 2022',
        source_ref: sourceRef('Software Engineer, Sep 2022 to now', 'LinkedIn profile'),
      },
    ],
    resolution: 'unresolved',
    note: null,
    ...overrides,
  }
}

export function makeProfile(overrides: Partial<Profile> = {}): Profile {
  const records = overrides.records ?? [
    makeRecord(),
    makeRecord({
      record_id: 'rec-2',
      category: 'education',
      title: 'B.S. Computer Science',
      organization: 'Lakeshore State University',
      start_date: '2018',
      end_date: '2022',
      bullets: [],
      skills: [],
      source_ref: sourceRef('B.S. Computer Science, Lakeshore State University, 2018 - 2022'),
    }),
    makeRecord({
      record_id: 'rec-3',
      category: 'skill',
      title: 'Languages',
      organization: null,
      location: null,
      start_date: null,
      end_date: null,
      bullets: [],
      skills: ['Python', 'TypeScript'],
      source_ref: sourceRef('Languages: Python, TypeScript'),
    }),
  ]
  const conflicts = overrides.conflicts ?? []
  return {
    profile_id: 'prof-1',
    version: 1,
    status: 'draft',
    index_state: 'not_indexed',
    indexed_version: null,
    index_progress: { total: 0, embedded: 0 },
    index_error: null,
    contact: {
      name: 'Riley Example',
      email: 'riley@example.com',
      phone: null,
      location: 'Columbus, OH',
      links: ['https://example.com/riley'],
    },
    sources: [
      { source_id: 'src-1', label: 'Resume', source_type: 'resume', char_count: 1200, revision: 1 },
    ],
    review_summary: {
      needs_review_count: records.reduce(
        (count, record) =>
          count +
          (record.needs_review ? 1 : 0) +
          record.bullets.filter((bullet) => bullet.needs_review).length,
        0,
      ),
      unresolved_conflict_count: conflicts.filter((c) => c.resolution === 'unresolved').length,
    },
    created_at: '2026-10-07T12:00:00Z',
    updated_at: '2026-10-07T12:00:00Z',
    expires_at: futureIso(),
    ...overrides,
    records,
    conflicts,
  }
}
