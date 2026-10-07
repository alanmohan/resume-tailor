/**
 * The editable copy of a profile and the pure functions that work on it.
 *
 * The server profile is never mutated. The page keeps a "draft" in React
 * state, where every text field is a plain string (empty instead of null) so
 * inputs stay controlled, and converts it back to the API shape when saving.
 */
import type {
  Conflict,
  ConflictResolutionInput,
  Contact,
  Profile,
  ProfilePatchRequest,
  ProfileRecord,
  ProfileRecordInput,
  RecordCategory,
} from '@/lib/types'

export interface DraftBullet {
  /** Stable React key: the bullet_id, or a generated key for a new bullet. */
  key: string
  bullet_id: string | null
  text: string
}

export interface DraftRecord {
  /** Stable React key: the record_id, or a generated key for a new record. */
  key: string
  record_id: string | null
  category: RecordCategory
  title: string
  organization: string
  location: string
  start_date: string
  end_date: string
  summary: string
  bullets: DraftBullet[]
  skills: string[]
  /** Marked for removal; left out of the next save unless restored. */
  removed: boolean
}

export interface DraftContact {
  name: string
  email: string
  phone: string
  location: string
  /** One link per line. */
  links: string
}

export interface ProfileDraft {
  contact: DraftContact
  records: DraftRecord[]
}

export interface CategorySection {
  category: RecordCategory
  heading: string
  /** Used in "Add ..." and "New ..." labels. */
  singular: string
  titleLabel: string
  organizationLabel: string
}

/** Sections in display order. Skill records are groups: a label plus a list of skills. */
export const CATEGORY_SECTIONS: CategorySection[] = [
  {
    category: 'employment',
    heading: 'Experience',
    singular: 'role',
    titleLabel: 'Job title',
    organizationLabel: 'Employer',
  },
  {
    category: 'project',
    heading: 'Projects',
    singular: 'project',
    titleLabel: 'Project name',
    organizationLabel: 'Organization',
  },
  {
    category: 'publication',
    heading: 'Publications',
    singular: 'publication',
    titleLabel: 'Title',
    organizationLabel: 'Publisher or venue',
  },
  {
    category: 'education',
    heading: 'Education',
    singular: 'education entry',
    titleLabel: 'Degree or program',
    organizationLabel: 'Institution',
  },
  {
    category: 'certification',
    heading: 'Certifications',
    singular: 'certification',
    titleLabel: 'Certification',
    organizationLabel: 'Issuer',
  },
  {
    category: 'skill',
    heading: 'Skills',
    singular: 'skill group',
    titleLabel: 'Group label',
    organizationLabel: '',
  },
  {
    category: 'achievement',
    heading: 'Achievements',
    singular: 'achievement',
    titleLabel: 'Achievement',
    organizationLabel: 'Organization',
  },
]

let generatedKeys = 0

function newKey(): string {
  generatedKeys += 1
  return `new-${generatedKeys}`
}

export function newBullet(): DraftBullet {
  return { key: newKey(), bullet_id: null, text: '' }
}

export function newRecord(category: RecordCategory): DraftRecord {
  return {
    key: newKey(),
    record_id: null,
    category,
    title: '',
    organization: '',
    location: '',
    start_date: '',
    end_date: '',
    summary: '',
    bullets: [],
    skills: [],
    removed: false,
  }
}

function draftRecord(record: ProfileRecord): DraftRecord {
  return {
    key: record.record_id,
    record_id: record.record_id,
    category: record.category,
    title: record.title,
    organization: record.organization ?? '',
    location: record.location ?? '',
    start_date: record.start_date ?? '',
    end_date: record.end_date ?? '',
    summary: record.summary ?? '',
    bullets: record.bullets.map((bullet) => ({
      key: bullet.bullet_id,
      bullet_id: bullet.bullet_id,
      text: bullet.text,
    })),
    skills: [...record.skills],
    removed: false,
  }
}

export function draftFromProfile(profile: Profile): ProfileDraft {
  const { contact } = profile
  return {
    contact: {
      name: contact.name ?? '',
      email: contact.email ?? '',
      phone: contact.phone ?? '',
      location: contact.location ?? '',
      links: contact.links.join('\n'),
    },
    records: profile.records.map(draftRecord),
  }
}

/** Trimmed text, or null when nothing is left (the API's "no value"). */
function textOrNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

function nonBlankLines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '')
}

export function toContact(contact: DraftContact): Contact {
  return {
    name: textOrNull(contact.name),
    email: textOrNull(contact.email),
    phone: textOrNull(contact.phone),
    location: textOrNull(contact.location),
    links: nonBlankLines(contact.links),
  }
}

/** Removed records and blank bullets are left out; text is trimmed, never rewritten. */
export function toRecordInputs(records: DraftRecord[]): ProfileRecordInput[] {
  return records
    .filter((record) => !record.removed)
    .map((record) => ({
      record_id: record.record_id,
      category: record.category,
      title: record.title.trim(),
      organization: textOrNull(record.organization),
      location: textOrNull(record.location),
      start_date: textOrNull(record.start_date),
      end_date: textOrNull(record.end_date),
      summary: textOrNull(record.summary),
      bullets: record.bullets
        .filter((bullet) => bullet.text.trim() !== '')
        .map((bullet) => ({ bullet_id: bullet.bullet_id, text: bullet.text.trim() })),
      skills: record.skills.map((skill) => skill.trim()).filter((skill) => skill !== ''),
    }))
}

export function toPatchRequest(
  expectedVersion: number,
  draft: ProfileDraft,
  resolutions: ConflictResolutionInput[] = [],
): ProfilePatchRequest {
  const request: ProfilePatchRequest = {
    expected_version: expectedVersion,
    contact: toContact(draft.contact),
    records: toRecordInputs(draft.records),
  }
  if (resolutions.length > 0) request.conflict_resolutions = resolutions
  return request
}

/** True when saving the draft would change the stored profile. */
export function isDirty(draft: ProfileDraft, profile: Profile): boolean {
  const saved = draftFromProfile(profile)
  const comparable = (value: ProfileDraft) =>
    JSON.stringify([toContact(value.contact), toRecordInputs(value.records)])
  return comparable(draft) !== comparable(saved)
}

/** Problems that block saving, keyed by record key. Every record needs a title. */
export function validateDraft(draft: ProfileDraft): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const record of draft.records) {
    if (!record.removed && record.title.trim() === '') {
      errors[record.key] = 'Enter a title, or remove this record.'
    }
  }
  return errors
}

/** Whether the user changed any of a record's own fields (bullets are compared separately). */
export function recordFieldsChanged(draft: DraftRecord, original: ProfileRecord): boolean {
  const before = draftRecord(original)
  return (
    draft.title.trim() !== before.title.trim() ||
    draft.organization.trim() !== before.organization.trim() ||
    draft.location.trim() !== before.location.trim() ||
    draft.start_date.trim() !== before.start_date.trim() ||
    draft.end_date.trim() !== before.end_date.trim() ||
    draft.summary.trim() !== before.summary.trim() ||
    draft.skills.join('\n') !== before.skills.join('\n')
  )
}

export function unresolvedConflicts(conflicts: Conflict[]): Conflict[] {
  return conflicts.filter((conflict) => conflict.resolution === 'unresolved')
}

/** "Title at Organization" for headings and screen-reader labels. */
export function recordDisplayName(record: { title: string; organization: string | null }): string {
  const title = record.title.trim()
  const organization = record.organization?.trim()
  if (title && organization) return `${title} at ${organization}`
  return title || organization || 'Untitled record'
}

/** Indexing progress in the server's own counts; no percentage is ever invented. */
export function indexProgressLabel(profile: Profile): string {
  const { total, embedded } = profile.index_progress
  if (total === 0) return 'Building evidence records...'
  return `Embedding ${embedded} of ${total} evidence records`
}
