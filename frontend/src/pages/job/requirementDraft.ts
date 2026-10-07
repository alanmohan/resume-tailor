/**
 * The editable copy of a job's requirements and the pure functions on it.
 *
 * The job from the server is never mutated. The review screen keeps a draft
 * in React state and converts it back to the API shape when saving. The draft
 * remembers the job version it was copied from; that version is what the save
 * sends as `expected_version`, so a job that changed elsewhere is detected by
 * the server instead of being overwritten.
 */
import type {
  Job,
  JobPatchRequest,
  Requirement,
  RequirementCategory,
  RequirementImportance,
} from '@/lib/types'
import { MAX_JOB_FIELD_CHARS, textOrNull } from './jobFormSchema'

/** Longest requirement text the API accepts. */
export const MAX_REQUIREMENT_CHARS = 500

export interface Option<T extends string> {
  value: T
  label: string
}

export const CATEGORY_OPTIONS: Option<RequirementCategory>[] = [
  { value: 'skill', label: 'Skill' },
  { value: 'experience', label: 'Experience' },
  { value: 'education', label: 'Education' },
  { value: 'certification', label: 'Certification' },
  { value: 'responsibility', label: 'Responsibility' },
  { value: 'other', label: 'Other' },
]

/** In display order: the review lists required qualifications first. */
export const IMPORTANCE_OPTIONS: Option<RequirementImportance>[] = [
  { value: 'required', label: 'Required' },
  { value: 'preferred', label: 'Preferred' },
]

export interface DraftRequirement {
  /** Stable React key: the requirement_id, or a generated key for a new one. */
  key: string
  requirement_id: string | null
  text: string
  category: RequirementCategory
  importance: RequirementImportance
  /** Marked for removal; left out of the next save unless restored. */
  removed: boolean
}

export interface JobDraft {
  /** The job version this draft was copied from. */
  version: number
  title: string
  company: string
  requirements: DraftRequirement[]
}

let generatedKeys = 0

/** A blank requirement the user is adding; the server assigns its ID on save. */
export function newRequirement(importance: RequirementImportance): DraftRequirement {
  generatedKeys += 1
  return {
    key: `new-${generatedKeys}`,
    requirement_id: null,
    text: '',
    category: 'skill',
    importance,
    removed: false,
  }
}

export function draftFromJob(job: Job): JobDraft {
  return {
    version: job.version,
    title: job.title ?? '',
    company: job.company ?? '',
    requirements: job.requirements.map((requirement) => ({
      key: requirement.requirement_id,
      requirement_id: requirement.requirement_id,
      text: requirement.text,
      category: requirement.category,
      importance: requirement.importance,
      removed: false,
    })),
  }
}

/** The requirements that the next save will keep. */
export function activeRequirements(draft: JobDraft): DraftRequirement[] {
  return draft.requirements.filter((requirement) => !requirement.removed)
}

/** Removed requirements are left out; text is trimmed, never rewritten. */
export function toJobPatchRequest(draft: JobDraft): JobPatchRequest {
  return {
    expected_version: draft.version,
    title: textOrNull(draft.title),
    company: textOrNull(draft.company),
    requirements: activeRequirements(draft).map((requirement) => ({
      requirement_id: requirement.requirement_id,
      text: requirement.text.trim(),
      category: requirement.category,
      importance: requirement.importance,
    })),
  }
}

/**
 * True when saving the draft would change the stored job, or when the draft
 * was copied from an older version of it.
 */
export function isDirty(draft: JobDraft, job: Job): boolean {
  const comparable = (value: JobDraft) => JSON.stringify(toJobPatchRequest(value))
  return comparable(draft) !== comparable(draftFromJob(job))
}

export interface DraftErrors {
  title?: string
  company?: string
  /** There are more requirements than the server allows. */
  count?: string
  /** Messages for individual requirements, keyed by DraftRequirement.key. */
  requirements: Record<string, string>
}

/** Problems that block saving. Removed requirements are not checked. */
export function validateDraft(draft: JobDraft, maxRequirements: number): DraftErrors {
  const errors: DraftErrors = { requirements: {} }
  if (draft.title.trim().length > MAX_JOB_FIELD_CHARS) {
    errors.title = `Keep the role title to ${MAX_JOB_FIELD_CHARS} characters or fewer.`
  }
  if (draft.company.trim().length > MAX_JOB_FIELD_CHARS) {
    errors.company = `Keep the company name to ${MAX_JOB_FIELD_CHARS} characters or fewer.`
  }

  const active = activeRequirements(draft)
  if (active.length > maxRequirements) {
    const excess = active.length - maxRequirements
    errors.count = `A job can have at most ${maxRequirements} requirements. Remove ${excess} to save.`
  }
  for (const requirement of active) {
    const length = requirement.text.trim().length
    if (length === 0) {
      errors.requirements[requirement.key] = 'Describe the requirement, or remove it.'
    } else if (length > MAX_REQUIREMENT_CHARS) {
      errors.requirements[requirement.key] =
        `Keep the requirement to ${MAX_REQUIREMENT_CHARS} characters or fewer (it has ${length}).`
    }
  }
  return errors
}

export function hasErrors(errors: DraftErrors): boolean {
  return (
    errors.title !== undefined ||
    errors.company !== undefined ||
    errors.count !== undefined ||
    Object.keys(errors.requirements).length > 0
  )
}

/**
 * The number shown for each requirement, by key. Numbers follow display
 * order: every required requirement first, then every preferred one.
 */
export function requirementPositions(requirements: DraftRequirement[]): Map<string, number> {
  const ordered = IMPORTANCE_OPTIONS.flatMap((option) =>
    requirements.filter((requirement) => requirement.importance === option.value),
  )
  return new Map(ordered.map((requirement, index) => [requirement.key, index + 1]))
}

/**
 * Whether the requirement differs from what the analysis extracted. The server
 * applies the same rule when saving: a changed text, category or importance
 * marks the requirement as user edited.
 */
export function isUserEdited(draft: DraftRequirement, stored: Requirement): boolean {
  return (
    stored.user_edited ||
    stored.text.trim() !== draft.text.trim() ||
    stored.category !== draft.category ||
    stored.importance !== draft.importance
  )
}
