import { vi } from 'vitest'
import type { Claim, CoverageItem, Evidence, Generation, ResumeEntry } from '@/lib/types'
import { futureIso } from '@/test/mockApi'
import { listClaims } from '../workspaceModel'

/** Fictional draft data for the Workspace tests. No real person, employer or job is described. */

export const GENERATION_ID = 'gen-1'

export function makeClaim(overrides: Partial<Claim> = {}): Claim {
  return {
    item_id: 'item-1',
    text: 'Built a search service in Python',
    evidence_ids: ['ev-1'],
    validation_status: 'supported',
    warnings: [],
    user_edited: false,
    ...overrides,
  }
}

export function makeEntry(overrides: Partial<ResumeEntry> = {}): ResumeEntry {
  return {
    entry_id: 'entry-1',
    record_id: 'rec-1',
    category: 'employment',
    heading: 'Software Engineer',
    subheading: 'Northwind Robotics',
    location: 'Columbus, OH',
    date_range: 'Aug 2022 - Present',
    bullets: [],
    ...overrides,
  }
}

export function makeCoverageItem(overrides: Partial<CoverageItem> = {}): CoverageItem {
  return {
    requirement_id: 'req-1',
    requirement_text: 'Experience building Python services',
    importance: 'required',
    status: 'supported',
    evidence_ids: ['ev-1'],
    rationale: 'The profile describes a Python search service.',
    user_corrected: false,
    note: null,
    ...overrides,
  }
}

export function makeEvidence(overrides: Partial<Evidence> = {}): Evidence {
  return {
    evidence_id: 'ev-2',
    excerpt: 'Built React and TypeScript dashboards used by 35 internal support agents',
    text: 'Software Engineer at Northwind Robotics: built React and TypeScript dashboards used by 35 internal support agents',
    category: 'employment',
    source: { source_id: 'src-1', label: 'My resume', source_type: 'resume', start: 120, end: 192 },
    provenance: 'extracted',
    parent: {
      record_id: 'rec-1',
      category: 'employment',
      title: 'Software Engineer',
      organization: 'Northwind Robotics',
    },
    tags: ['React', 'TypeScript'],
    profile_version: 1,
    ...overrides,
  }
}

/**
 * A completed, validated draft at revision 3 with one statement that needs
 * review ("b-2"). Coverage: 1 supported, 1 partial, 1 missing, 1 uncertain,
 * which gives 100 x (1 + 0.5 x 1) / 3 = 50%.
 */
export function makeGeneration(overrides: Partial<Generation> = {}): Generation {
  return {
    generation_id: GENERATION_ID,
    status: 'completed',
    error: null,
    revision: 3,
    job_id: 'job-1',
    job_version: 1,
    job_title: 'Backend Engineer',
    company: 'Acme Analytics',
    profile_id: 'prof-1',
    profile_version: 1,
    stale: false,
    stale_reasons: [],
    provider_mode: 'openai',
    model: 'gpt-6-luna',
    retrieved_evidence_ids: ['ev-1', 'ev-2', 'ev-3', 'ev-4', 'ev-5'],
    resume: {
      contact: {
        name: 'Riley Example',
        email: 'riley@example.com',
        phone: null,
        location: 'Columbus, OH',
        links: ['https://example.com/riley'],
      },
      summary: [
        makeClaim({
          item_id: 'sum-1',
          text: 'Software engineer with four years of experience building Python services.',
        }),
        makeClaim({
          item_id: 'sum-2',
          text: 'Looking to bring that experience to a data-focused team.',
          evidence_ids: [],
          validation_status: 'not_applicable',
        }),
      ],
      experience: [
        makeEntry({
          bullets: [
            makeClaim({
              item_id: 'b-1',
              text: 'Built a search service in Python used by 35 support agents',
              evidence_ids: ['ev-1', 'ev-2'],
            }),
            makeClaim({
              item_id: 'b-2',
              text: 'Cut the nightly reporting job from 3 hours to 45 minutes',
              evidence_ids: ['ev-3'],
              validation_status: 'needs_review',
              warnings: ['"45 minutes" does not appear in the cited evidence'],
            }),
          ],
        }),
      ],
      projects: [
        makeEntry({
          entry_id: 'entry-2',
          record_id: 'rec-2',
          category: 'project',
          heading: 'Trail Map App',
          subheading: 'Personal project',
          location: null,
          date_range: '2024',
          bullets: [
            makeClaim({
              item_id: 'p-1',
              text: 'Built semantic search over 12,000 trip reports',
              evidence_ids: ['ev-4'],
            }),
          ],
        }),
      ],
      education: [
        makeEntry({
          entry_id: 'entry-3',
          record_id: 'rec-3',
          category: 'education',
          heading: 'B.S. Computer Science',
          subheading: 'Lakeshore State University',
          location: null,
          date_range: '2018 - 2022',
        }),
      ],
      certifications: [],
      skills: [
        makeClaim({ item_id: 'sk-1', text: 'Python', evidence_ids: ['ev-5'] }),
        makeClaim({ item_id: 'sk-2', text: 'TypeScript', evidence_ids: ['ev-5'] }),
      ],
    },
    cover_letter: {
      paragraphs: [
        makeClaim({
          item_id: 'cl-1',
          text: 'Dear Hiring Manager,',
          evidence_ids: [],
          validation_status: 'not_applicable',
        }),
        makeClaim({
          item_id: 'cl-2',
          text: 'At Northwind Robotics I built a Python search service for the support team.',
          evidence_ids: ['ev-1'],
        }),
      ],
    },
    coverage: [
      makeCoverageItem(),
      makeCoverageItem({
        requirement_id: 'req-2',
        requirement_text: 'Experience with Kubernetes',
        status: 'missing',
        evidence_ids: [],
        rationale: 'Nothing in the profile mentions Kubernetes.',
      }),
      makeCoverageItem({
        requirement_id: 'req-3',
        requirement_text: 'TypeScript',
        importance: 'preferred',
        status: 'partial',
        evidence_ids: ['ev-5'],
        rationale: 'TypeScript is listed as a skill, but no role describes using it.',
      }),
      makeCoverageItem({
        requirement_id: 'req-4',
        requirement_text: 'Strong communication skills',
        importance: 'preferred',
        status: 'uncertain',
        evidence_ids: [],
        rationale: 'The requirement is too general to assess from the profile.',
      }),
    ],
    coverage_summary: { supported: 1, partial: 1, missing: 1, uncertain: 1, assessed: 3, percent: 50 },
    validation: {
      state: 'validated',
      needs_review_count: 1,
      unsupported_count: 0,
      user_edited_count: 0,
      validated_at: '2026-10-07T16:12:00Z',
    },
    omitted_claims: [],
    warnings: [],
    usage: { input_tokens: 4200, output_tokens: 1800, embedding_tokens: 300, provider_calls: 3 },
    created_at: '2026-10-07T16:11:30Z',
    updated_at: '2026-10-07T16:12:00Z',
    expires_at: futureIso(),
    ...overrides,
  }
}

/** A copy of the draft in which one statement has the given changes. */
export function withClaim(generation: Generation, itemId: string, change: Partial<Claim>): Generation {
  const copy = structuredClone(generation)
  for (const { claim } of listClaims(copy)) {
    if (claim.item_id === itemId) Object.assign(claim, change)
  }
  return copy
}

/** A copy of the draft in which every statement is supported, so nothing is flagged. */
export function withNothingFlagged(generation: Generation): Generation {
  const copy = withClaim(generation, 'b-2', { validation_status: 'supported', warnings: [] })
  copy.validation = { ...copy.validation, needs_review_count: 0 }
  return copy
}

/**
 * Make `window.matchMedia` report a wide screen, so the page renders its
 * two-pane layout. Without this the test environment reports no match and
 * the page renders the single-column (mobile) layout. Undone after each test
 * by the shared setup.
 */
export function stubWideScreen(): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('min-width'),
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }))
}
