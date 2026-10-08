import type {
  Generation,
  GenerationSummary,
  Job,
  Profile,
  Requirement,
} from '@/lib/types'
import { makeProfile } from '@/test/fixtures'
import { futureIso } from '@/test/mockApi'

/** Fictional job data for tests. The company and the posting do not exist. */

export const JOB_DESCRIPTION = `About the role
Fernhollow AI builds search tools for customer support teams.

Requirements
- Strong Python skills and experience building REST APIs
- Experience with Docker

Preferred qualifications
- Experience with AWS`

/** The span of `excerpt` inside `description`, as the API reports it. */
export function sourceSpan(excerpt: string, description = JOB_DESCRIPTION) {
  const start = description.indexOf(excerpt)
  if (start === -1) throw new Error(`"${excerpt}" is not part of the description`)
  return { start, end: start + excerpt.length, excerpt }
}

export function makeRequirement(overrides: Partial<Requirement> = {}): Requirement {
  return {
    requirement_id: 'req-1',
    text: 'Strong Python skills',
    category: 'skill',
    importance: 'required',
    inferred: false,
    keywords: [],
    source_span: null,
    user_edited: false,
    ...overrides,
  }
}

/** Two required and two preferred requirements; the last one is inferred. */
export function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    job_id: 'job-1',
    version: 1,
    company: 'Fernhollow AI',
    title: 'Applied Machine Learning Engineer',
    description: JOB_DESCRIPTION,
    role_summary: 'Builds retrieval features for support teams.',
    requirements: [
      makeRequirement({
        requirement_id: 'req-1',
        text: 'Strong Python skills',
        source_span: sourceSpan('Strong Python skills and experience building REST APIs'),
      }),
      makeRequirement({
        requirement_id: 'req-2',
        text: 'Experience with Docker',
        source_span: sourceSpan('Experience with Docker'),
      }),
      makeRequirement({
        requirement_id: 'req-3',
        text: 'Experience with AWS',
        importance: 'preferred',
        source_span: sourceSpan('Experience with AWS'),
      }),
      makeRequirement({
        requirement_id: 'req-4',
        text: 'Comfortable working with support teams',
        category: 'responsibility',
        importance: 'preferred',
        inferred: true,
        source_span: null,
      }),
    ],
    created_at: '2026-10-07T12:00:00.000Z',
    updated_at: '2026-10-07T12:00:00.000Z',
    expires_at: futureIso(),
    ...overrides,
  }
}

/** A completed generation with empty documents; these tests never render them. */
export function makeGeneration(overrides: Partial<Generation> = {}): Generation {
  return {
    generation_id: 'gen-1',
    status: 'completed',
    error: null,
    revision: 1,
    job_id: 'job-1',
    job_version: 1,
    job_title: 'Applied Machine Learning Engineer',
    company: 'Fernhollow AI',
    profile_id: 'prof-1',
    profile_version: 1,
    stale: false,
    stale_reasons: [],
    provider_mode: 'openai',
    model: 'gpt-6-luna',
    retrieved_evidence_ids: [],
    resume: null,
    cover_letter: null,
    coverage: [],
    coverage_summary: {
      supported: 0,
      partial: 0,
      missing: 0,
      uncertain: 0,
      assessed: 0,
      percent: null,
    },
    validation: {
      state: 'validated',
      needs_review_count: 0,
      unsupported_count: 0,
      user_edited_count: 0,
      validated_at: null,
    },
    omitted_claims: [],
    warnings: [],
    usage: { input_tokens: 0, output_tokens: 0, embedding_tokens: 0, provider_calls: 0 },
    created_at: '2026-10-07T13:00:00.000Z',
    updated_at: '2026-10-07T13:00:00.000Z',
    expires_at: futureIso(),
    ...overrides,
  }
}

export function makeGenerationSummary(
  overrides: Partial<GenerationSummary> = {},
): GenerationSummary {
  return {
    generation_id: 'gen-1',
    job_id: 'job-1',
    job_title: 'Applied Machine Learning Engineer',
    company: 'Fernhollow AI',
    status: 'completed',
    stale: false,
    created_at: '2026-10-07T13:00:00.000Z',
    ...overrides,
  }
}

/** A profile that is confirmed and fully indexed, so generation is allowed. */
export function readyProfile(): Profile {
  return makeProfile({
    status: 'confirmed',
    index_state: 'indexed',
    indexed_version: 1,
    index_progress: { total: 5, embedded: 5 },
  })
}
