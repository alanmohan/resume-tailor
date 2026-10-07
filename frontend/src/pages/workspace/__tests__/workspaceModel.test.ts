import { describe, expect, it } from 'vitest'
import {
  contactDetails,
  countLabel,
  coveragePercentLabel,
  documentPlainText,
  entrySections,
  evidenceTitle,
  formatDateTime,
  isFlagged,
  jobLabel,
  letterSignOff,
  listClaims,
  needsExportReview,
  numberEvidence,
  sectionLabel,
  staleExplanation,
} from '../workspaceModel'
import { makeClaim, makeEntry, makeGeneration, withClaim, withNothingFlagged } from './fixtures'

describe('entrySections', () => {
  it('keeps the display order and leaves out empty sections', () => {
    const sections = entrySections(makeGeneration().resume!)

    expect(sections.map((section) => section.title)).toEqual(['Experience', 'Projects', 'Education'])
  })

  it('names the projects section after publications only when one is present', () => {
    const resume = makeGeneration().resume!
    resume.projects.push(makeEntry({ entry_id: 'entry-9', category: 'publication', heading: 'A Paper' }))

    expect(entrySections(resume).map((section) => section.title)).toContain('Projects and publications')
  })

  it('names awards in the projects section title when it holds an achievement', () => {
    const titleWith = (...categories: string[]) => {
      const resume = makeGeneration().resume!
      categories.forEach((category, index) =>
        resume.projects.push(makeEntry({ entry_id: `entry-extra-${index}`, category })),
      )
      return entrySections(resume).find((section) => section.key === 'projects')!.title
    }

    expect(titleWith()).toBe('Projects')
    expect(titleWith('achievement')).toBe('Projects and awards')
    expect(titleWith('publication', 'achievement')).toBe('Projects, publications and awards')
  })

  it('marks only experience and projects as sections the server can regenerate', () => {
    const resume = makeGeneration().resume!
    resume.certifications.push(makeEntry({ entry_id: 'entry-8', category: 'certification' }))

    const regenerable = entrySections(resume).map((section) => [section.key, section.regenerable])
    expect(regenerable).toEqual([
      ['experience', true],
      ['projects', true],
      ['education', false],
      ['certifications', false],
    ])
  })
})

describe('listClaims', () => {
  it('lists every statement in reading order with its document and place', () => {
    const claims = listClaims(makeGeneration())

    expect(claims.map(({ claim }) => claim.item_id)).toEqual([
      'sum-1',
      'sum-2',
      'b-1',
      'b-2',
      'p-1',
      'sk-1',
      'sk-2',
      'cl-1',
      'cl-2',
    ])
    expect(claims[2]).toMatchObject({
      documentKind: 'resume',
      place: 'Experience: Software Engineer, Northwind Robotics',
    })
    expect(claims[5].place).toBe('Skills')
    expect(claims[8]).toMatchObject({ documentKind: 'cover_letter', place: 'Paragraph 2' })
  })

  it('copes with a draft that has no documents', () => {
    expect(listClaims(makeGeneration({ resume: null, cover_letter: null }))).toEqual([])
  })
})

describe('isFlagged', () => {
  it.each([
    ['supported', false],
    ['not_applicable', false],
    ['needs_review', true],
    ['unsupported', true],
    ['user_edited', true],
  ] as const)('%s -> %s', (status, expected) => {
    expect(isFlagged(makeClaim({ validation_status: status }))).toBe(expected)
  })
})

describe('numberEvidence', () => {
  it('numbers evidence by its position in the retrieved context', () => {
    const numbers = numberEvidence(makeGeneration())

    expect([...numbers.entries()]).toEqual([
      ['ev-1', 1],
      ['ev-2', 2],
      ['ev-3', 3],
      ['ev-4', 4],
      ['ev-5', 5],
    ])
    expect(evidenceTitle(numbers, 'ev-3')).toBe('Evidence 3')
    expect(evidenceTitle(numbers, 'unknown')).toBe('Evidence')
  })

  it('keeps the numbers when a statement stops citing a record', () => {
    const generation = makeGeneration()
    generation.resume!.experience[0].bullets[0].evidence_ids = ['ev-2']

    expect(numberEvidence(generation).get('ev-2')).toBe(2)
  })

  it('still numbers a cited ID that is missing from the retrieved list', () => {
    const generation = makeGeneration({ retrieved_evidence_ids: ['ev-1'] })

    const numbers = numberEvidence(generation)
    expect(numbers.get('ev-1')).toBe(1)
    expect(numbers.get('ev-2')).toBe(2)
    expect(numbers.size).toBe(5)
  })
})

describe('documentPlainText', () => {
  it('writes the resume as plain text without review markers', () => {
    expect(documentPlainText(makeGeneration(), 'resume')).toBe(
      [
        'Riley Example',
        'riley@example.com | Columbus, OH | https://example.com/riley',
        '',
        'SUMMARY',
        'Software engineer with four years of experience building Python services. Looking to bring that experience to a data-focused team.',
        '',
        'EXPERIENCE',
        'Software Engineer, Northwind Robotics',
        'Columbus, OH | Aug 2022 - Present',
        '- Built a search service in Python used by 35 support agents',
        '- Cut the nightly reporting job from 3 hours to 45 minutes',
        '',
        'PROJECTS',
        'Trail Map App, Personal project',
        '2024',
        '- Built semantic search over 12,000 trip reports',
        '',
        'EDUCATION',
        'B.S. Computer Science, Lakeshore State University',
        '2018 - 2022',
        '',
        'SKILLS',
        'Python, TypeScript',
      ].join('\n'),
    )
  })

  it('writes the cover letter as the letterhead, its paragraphs and the sign-off', () => {
    expect(documentPlainText(makeGeneration(), 'cover_letter')).toBe(
      [
        'Riley Example',
        'riley@example.com | Columbus, OH | https://example.com/riley',
        '',
        'Dear Hiring Manager,',
        '',
        'At Northwind Robotics I built a Python search service for the support team.',
        '',
        'Sincerely,',
        'Riley Example',
      ].join('\n'),
    )
  })

  it('titles the copied projects section after the kinds of record in it', () => {
    const generation = makeGeneration()
    generation.resume!.projects.push(
      makeEntry({ entry_id: 'entry-9', category: 'achievement', heading: 'Hack Night, second place' }),
    )

    expect(documentPlainText(generation, 'resume')).toContain('PROJECTS AND AWARDS\n')
  })

  it('returns an empty string when the document was not generated', () => {
    expect(documentPlainText(makeGeneration({ resume: null, cover_letter: null }), 'resume')).toBe('')
  })
})

describe('letterSignOff', () => {
  const letter = makeGeneration().cover_letter!
  const contact = makeGeneration().resume!.contact

  it('closes the letter with "Sincerely," and the confirmed name', () => {
    expect(letterSignOff(letter, contact)).toEqual(['Sincerely,', 'Riley Example'])
  })

  it('adds nothing without a confirmed name', () => {
    expect(letterSignOff(letter, { ...contact, name: null })).toEqual([])
    expect(letterSignOff(letter, { ...contact, name: '   ' })).toEqual([])
    expect(letterSignOff(letter, null)).toEqual([])
  })

  it('adds nothing when the last paragraph already signs off, or the letter is empty', () => {
    const signed = { paragraphs: [...letter.paragraphs, makeClaim({ item_id: 'cl-9', text: 'Best regards, Riley' })] }

    expect(letterSignOff(signed, contact)).toEqual([])
    expect(letterSignOff({ paragraphs: [] }, contact)).toEqual([])
  })
})

describe('needsExportReview', () => {
  const clean = () => withNothingFlagged(makeGeneration())

  it('is false for a validated draft with no flagged statement', () => {
    expect(needsExportReview(clean())).toBe(false)
  })

  it('is true for a flagged statement of any kind', () => {
    for (const status of ['needs_review', 'unsupported', 'user_edited'] as const) {
      expect(needsExportReview(withClaim(clean(), 'sum-1', { validation_status: status }))).toBe(true)
    }
  })

  it('is true while edits wait for revalidation or the server counts an unsupported statement', () => {
    const edited = clean()
    edited.validation.state = 'needs_revalidation'
    const unsupported = clean()
    unsupported.validation.unsupported_count = 1

    expect(needsExportReview(edited)).toBe(true)
    expect(needsExportReview(unsupported)).toBe(true)
  })
})

describe('wording helpers', () => {
  it('lists contact details in a fixed order and skips blanks', () => {
    expect(
      contactDetails({ name: 'Riley', email: null, phone: '555-0100', location: ' ', links: ['a.example'] }),
    ).toEqual(['555-0100', 'a.example'])
  })

  it('shows the coverage percentage, or "Unavailable" for a zero denominator', () => {
    const base = { supported: 0, partial: 0, missing: 0, uncertain: 0, assessed: 0 }

    expect(coveragePercentLabel({ ...base, percent: 62.5 })).toBe('62.5%')
    expect(coveragePercentLabel({ ...base, percent: 100 })).toBe('100%')
    expect(coveragePercentLabel({ ...base, percent: 0 })).toBe('0%')
    expect(coveragePercentLabel({ ...base, percent: null })).toBe('Unavailable')
  })

  it('names the reason a draft is stale', () => {
    expect(staleExplanation(['profile_changed'])).toBe(
      'Your profile changed after this draft was generated.',
    )
    expect(staleExplanation(['job_changed'])).toBe(
      'The target job changed after this draft was generated.',
    )
    expect(staleExplanation(['profile_changed', 'job_changed'])).toBe(
      'Your profile and the target job changed after this draft was generated.',
    )
    expect(staleExplanation([])).toBe(
      'Your profile or the target job changed after this draft was generated.',
    )
  })

  it('labels the target job from whatever the job has', () => {
    expect(jobLabel(makeGeneration())).toBe('Backend Engineer at Acme Analytics')
    expect(jobLabel(makeGeneration({ company: null }))).toBe('Backend Engineer')
    expect(jobLabel(makeGeneration({ job_title: null }))).toBe('Acme Analytics')
    expect(jobLabel(makeGeneration({ job_title: null, company: null }))).toBeNull()
  })

  it('formats timestamps and rejects unparseable ones', () => {
    expect(formatDateTime('2026-10-07T16:12:00Z')).toMatch(/2026/)
    expect(formatDateTime('not a date')).toBeNull()
  })

  it('makes section keys and counts readable', () => {
    expect(sectionLabel('cover_letter')).toBe('Cover letter')
    expect(sectionLabel('experience')).toBe('Experience')
    expect(countLabel(1, 'warning')).toBe('1 warning')
    expect(countLabel(3, 'warning')).toBe('3 warnings')
    expect(countLabel(2, 'statement has', 'statements have')).toBe('2 statements have')
  })
})
