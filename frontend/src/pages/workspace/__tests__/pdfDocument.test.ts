import { describe, expect, it } from 'vitest'
import type { Generation } from '@/lib/types'
import {
  MARGIN,
  PAGE_HEIGHT,
  PAGE_WIDTH,
  buildPdf,
  pdfFileName,
  pdfSafeText,
  type PdfLayout,
} from '../pdfDocument'
import { makeClaim, makeEntry, makeGeneration } from './fixtures'

/** Every line of the file, in order, as plain strings. */
function textOf(layout: PdfLayout): string[] {
  return layout.pages.flat().map((line) => line.text)
}

/** All text joined with spaces, so a sentence that wrapped can still be found. */
function flowOf(layout: PdfLayout): string {
  return textOf(layout).join(' ')
}

function expectInsideMargins(layout: PdfLayout): void {
  for (const line of layout.pages.flat()) {
    expect(line.x, line.text).toBeGreaterThanOrEqual(MARGIN - 0.01)
    expect(line.x + line.width, line.text).toBeLessThanOrEqual(PAGE_WIDTH - MARGIN + 0.01)
    expect(line.y, line.text).toBeGreaterThan(MARGIN)
    expect(line.y, line.text).toBeLessThanOrEqual(PAGE_HEIGHT - MARGIN)
  }
}

/** A resume with enough roles and bullets to need several pages. */
function longResume(): Generation {
  const generation = makeGeneration()
  const roles = Array.from({ length: 14 }, (_, role) =>
    makeEntry({
      entry_id: `entry-long-${role}`,
      heading: `Engineer ${role + 1}`,
      date_range: `20${10 + role} - 20${11 + role}`,
      bullets: Array.from({ length: 5 }, (_, bullet) =>
        makeClaim({
          item_id: `long-${role}-${bullet}`,
          text: `Role ${role + 1} bullet ${bullet + 1}: delivered a measurable improvement to a service that many people relied on every day, and wrote down how it was done.`,
        }),
      ),
    }),
  )
  if (generation.resume) generation.resume.experience = roles
  return generation
}

describe('buildPdf for a resume', () => {
  it('writes the name, contact line, section headings, entries, bullets and skills in order', () => {
    const layout = buildPdf(makeGeneration(), 'resume')
    const lines = textOf(layout)

    expect(layout.pages).toHaveLength(1)
    expect(lines[0]).toBe('Riley Example')
    expect(lines[1]).toBe('riley@example.com | Columbus, OH | https://example.com/riley')
    // The same sections and titles as on screen, without the empty Certifications.
    const headings = ['SUMMARY', 'EXPERIENCE', 'PROJECTS', 'EDUCATION', 'SKILLS']
    expect(lines.filter((line) => headings.includes(line))).toEqual(headings)
    expect(lines).not.toContain('CERTIFICATIONS')

    const flow = flowOf(layout)
    expect(flow).toContain(
      'Software engineer with four years of experience building Python services. Looking to bring that experience to a data-focused team.',
    )
    for (const bullet of [
      'Built a search service in Python used by 35 support agents',
      'Cut the nightly reporting job from 3 hours to 45 minutes',
      'Built semantic search over 12,000 trip reports',
    ]) {
      expect(lines).toContain(bullet)
    }
    expect(lines).toContain('Trail Map App')
    expect(lines).toContain('Personal project')
    expect(lines).toContain('B.S. Computer Science')
    expect(lines.at(-1)).toBe('Python, TypeScript')
    expectInsideMargins(layout)
  })

  it('puts the date range on the heading\'s line, ending at the right margin', () => {
    const layout = buildPdf(makeGeneration(), 'resume')
    const lines = layout.pages.flat()
    const heading = lines.find((line) => line.text === 'Trail Map App')
    const date = lines.find((line) => line.text === '2024')

    expect(heading?.x).toBe(MARGIN)
    expect(date?.y).toBe(heading?.y)
    expect((date?.x ?? 0) + (date?.width ?? 0)).toBeCloseTo(PAGE_WIDTH - MARGIN, 3)
  })

  it('indents bullets and their wrapped lines alike', () => {
    const generation = makeGeneration()
    const long =
      'Rebuilt the ingestion pipeline so that every nightly import finished before the morning shift started, which removed a recurring delay for the whole support organisation'
    if (generation.resume) generation.resume.experience[0].bullets[0].text = long
    const layout = buildPdf(generation, 'resume')
    const lines = layout.pages.flat()
    const first = lines.findIndex((line) => line.text.startsWith('Rebuilt the ingestion'))
    const wrapped = [lines[first], lines[first + 1]]

    // One sentence became at least two lines, both at the hanging indent.
    expect(wrapped.map((line) => line.text).join(' ')).toContain('finished before the morning')
    expect(wrapped[0].text).not.toBe(long)
    expect(wrapped[0].x).toBeGreaterThan(MARGIN)
    expect(wrapped[1].x).toBe(wrapped[0].x)
    expect(flowOf(layout)).toContain(long)
    expectInsideMargins(layout)
  })

  it('uses the derived projects title and includes certifications when there are some', () => {
    const generation = makeGeneration()
    if (generation.resume) {
      generation.resume.projects.push(
        makeEntry({ entry_id: 'entry-pub', category: 'publication', heading: 'Search at Scale', bullets: [] }),
      )
      generation.resume.certifications = [
        makeEntry({ entry_id: 'entry-cert', category: 'certification', heading: 'Cloud Practitioner', bullets: [] }),
      ]
    }
    const lines = textOf(buildPdf(generation, 'resume'))

    expect(lines).toContain('PROJECTS AND PUBLICATIONS')
    expect(lines).toContain('CERTIFICATIONS')
    expect(lines).toContain('Cloud Practitioner')
  })

  it('wraps text without a break opportunity inside the margins', () => {
    const generation = makeGeneration()
    if (generation.resume) {
      generation.resume.contact.links = [`https://example.com/${'a'.repeat(160)}`]
      generation.resume.skills = [makeClaim({ item_id: 'sk-long', text: 'x'.repeat(300) })]
    }
    const layout = buildPdf(generation, 'resume')

    expectInsideMargins(layout)
    expect(textOf(layout).join('')).toContain('x'.repeat(300))
  })

  it('breaks a long resume over several pages, with every line whole and entries kept together', () => {
    const generation = longResume()
    const layout = buildPdf(generation, 'resume')

    expect(layout.pages.length).toBeGreaterThan(1)
    expect(layout.doc.getNumberOfPages()).toBe(layout.pages.length)
    expectInsideMargins(layout)
    // Nothing was lost at a page break.
    const flow = flowOf(layout)
    for (const entry of generation.resume?.experience ?? []) {
      for (const bullet of entry.bullets) expect(flow).toContain(bullet.text)
    }
    // No page ends with an entry heading (or its date) cut off from its first bullet.
    for (const page of layout.pages.slice(0, -1)) {
      expect(page.at(-1)?.text).not.toMatch(/^(Engineer \d+|20\d\d - 20\d\d|Northwind Robotics.*)$/)
    }
    // A section title is not left alone at the bottom of a page either.
    for (const page of layout.pages) expect(page.at(-1)?.text).not.toMatch(/^[A-Z ,]+$/)
  })

  it('contains no review markers', () => {
    const flow = flowOf(buildPdf(makeGeneration(), 'resume'))

    expect(flow).not.toMatch(/Needs review|Supported|Evidence \d|does not appear in the cited evidence/)
  })

  it('refuses to make a file for a document the draft does not have', () => {
    expect(() => buildPdf(makeGeneration({ resume: null }), 'resume')).toThrow('no resume')
    expect(() => buildPdf(makeGeneration({ cover_letter: null }), 'cover_letter')).toThrow(
      'no cover letter',
    )
  })
})

describe('buildPdf for a cover letter', () => {
  it('writes the name and contact, each paragraph and the sign-off', () => {
    const layout = buildPdf(makeGeneration(), 'cover_letter')
    const lines = textOf(layout)

    expect(lines.slice(0, 2)).toEqual([
      'Riley Example',
      'riley@example.com | Columbus, OH | https://example.com/riley',
    ])
    expect(lines).toContain('Dear Hiring Manager,')
    expect(flowOf(layout)).toContain(
      'At Northwind Robotics I built a Python search service for the support team.',
    )
    expect(lines.slice(-2)).toEqual(['Sincerely,', 'Riley Example'])
    // The resume's sections are not part of the letter.
    expect(lines).not.toContain('SUMMARY')
    expectInsideMargins(layout)
  })

  it('leaves the sign-off out when the letter already signs off', () => {
    const generation = makeGeneration()
    generation.cover_letter?.paragraphs.push(makeClaim({ item_id: 'cl-3', text: 'Best regards, Riley' }))

    expect(textOf(buildPdf(generation, 'cover_letter')).at(-1)).toBe('Best regards, Riley')
  })
})

describe('pdfSafeText', () => {
  it('replaces characters the built-in fonts cannot draw with plain equivalents', () => {
    expect(pdfSafeText('\u201Csmart\u201D \u2018quotes\u2019')).toBe('"smart" \'quotes\'')
    expect(pdfSafeText('2019\u20132024 \u2014 now')).toBe('2019-2024 - now')
    expect(pdfSafeText('\u2022 item\u00A0one\u2026')).toBe('- item one...')
    expect(pdfSafeText('A \u2192 B \u2190 C')).toBe('A -> B <- C')
    expect(pdfSafeText('tab\there')).toBe('tab here')
  })

  it('keeps Western European letters and marks anything else', () => {
    expect(pdfSafeText('Zo\u00E9 M\u00FCller, caf\u00E9')).toBe('Zo\u00E9 M\u00FCller, caf\u00E9')
    expect(pdfSafeText('\u65E5 ok')).toBe('? ok')
  })

  it('is applied to everything that is written', () => {
    const generation = makeGeneration()
    if (generation.resume) {
      generation.resume.contact.name = 'Riley \u2018Rye\u2019 Example'
      generation.resume.experience[0].date_range = '2021 \u2013 2024'
      generation.resume.experience[0].bullets[0].text = 'Cut latency \u2192 40 ms \u2014 measured'
    }
    const lines = textOf(buildPdf(generation, 'resume'))

    expect(lines[0]).toBe("Riley 'Rye' Example")
    expect(lines).toContain('2021 - 2024')
    expect(lines).toContain('Cut latency -> 40 ms - measured')
    // Only characters the built-in fonts have reached the file.
    for (const line of lines) expect(line).toMatch(/^[\x20-\x7E\xA1-\xFF]*$/)
  })
})

describe('pdfFileName', () => {
  it('names the file after the person and the document', () => {
    expect(pdfFileName(makeGeneration(), 'resume')).toBe('Riley Example - Resume.pdf')
    expect(pdfFileName(makeGeneration(), 'cover_letter')).toBe('Riley Example - Cover Letter.pdf')
  })

  it('removes characters that are unsafe in a file name', () => {
    const generation = makeGeneration()
    if (generation.resume) generation.resume.contact.name = ' ../Riley: "R" <Example>?*|\\ \n Jr. '

    expect(pdfFileName(generation, 'resume')).toBe('Riley R Example Jr - Resume.pdf')
  })

  it('falls back to the document name when no name is usable', () => {
    const unnamed = makeGeneration()
    if (unnamed.resume) unnamed.resume.contact.name = ' /: '

    expect(pdfFileName(unnamed, 'resume')).toBe('Resume.pdf')
    expect(pdfFileName(makeGeneration({ resume: null }), 'resume')).toBe('Resume.pdf')
    expect(pdfFileName(makeGeneration({ resume: null }), 'cover_letter')).toBe('Cover Letter.pdf')
  })
})
