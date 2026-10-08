/**
 * One document of a draft as a real PDF file with selectable text.
 *
 * The page is laid out here with jsPDF text calls: US Letter, one column,
 * black on white, built-in fonts. Sections, entries and titles come from
 * workspaceModel.ts, so the screen, the copied text and the PDF agree. The
 * file carries the document only: no evidence badges, statuses or controls.
 *
 * `buildPdf` does the layout and returns what it drew, so it can be tested
 * without a download; `downloadPdf` is the thin part that saves the file.
 * Workspace.tsx imports this module on the first download, which keeps jsPDF
 * out of the bundle every visitor loads.
 */
import { jsPDF } from 'jspdf'
import type { Contact, CoverLetter, Generation, Resume, ResumeEntry } from '@/lib/types'
import {
  contactDetails,
  entrySections,
  letterSignOff,
  type DocumentKind,
  type EntrySection,
} from './workspaceModel'

// ----------------------------------------------------------------- page

/** All measurements are in PDF points: 72 points are one inch. */
export const PAGE_WIDTH = 612 // US Letter, 8.5in
export const PAGE_HEIGHT = 792 // 11in
export const MARGIN = 54 // 0.75in
const CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

interface TextStyle {
  font: 'helvetica' | 'times'
  weight: 'normal' | 'bold'
  /** Font size in points. */
  size: number
}

const STYLE = {
  name: { font: 'helvetica', weight: 'bold', size: 20 },
  contact: { font: 'helvetica', weight: 'normal', size: 10 },
  sectionTitle: { font: 'helvetica', weight: 'bold', size: 10.5 },
  entryHeading: { font: 'times', weight: 'bold', size: 11 },
  // Same size as the entry heading: both sit on one line and share its baseline.
  entryDate: { font: 'times', weight: 'normal', size: 11 },
  body: { font: 'times', weight: 'normal', size: 11 },
} satisfies Record<string, TextStyle>

/** Vertical gaps, in points. */
const GAP = {
  afterHeader: 6,
  beforeSection: 14,
  afterSectionRule: 8,
  betweenEntries: 8,
  betweenParagraphs: 10,
  beforeSignOff: 14,
}

/** Space left of a bullet's text; the dot is drawn inside it. */
const BULLET_INDENT = 14
/** The least room between an entry heading and its right-aligned date. */
const DATE_GAP = 12

function lineHeight(style: TextStyle): number {
  return style.size * 1.35
}

// ----------------------------------------------------------------- text

/** Characters the built-in fonts cannot draw, with the plain text used instead. */
const PLAIN_EQUIVALENTS: [RegExp, string][] = [
  [/[\u2018\u2019\u201A\u2032]/g, "'"], // curly single quotes, prime
  [/[\u201C\u201D\u201E\u2033]/g, '"'], // curly double quotes, double prime
  [/[\u2010-\u2015\u2212]/g, '-'], // hyphens, en and em dashes, minus sign
  [/[\u2022\u2023\u25AA\u25CF\u25E6]/g, '-'], // bullets
  [/\u2026/g, '...'], // ellipsis
  [/\u2192/g, '->'], // arrows
  [/\u2190/g, '<-'],
  [/\u2194/g, '<->'],
  [/[\u200B-\u200D\uFEFF]/g, ''], // zero-width characters
  [/[\t\r\u00A0\u2000-\u200A\u202F\u3000]/g, ' '], // tabs, non-breaking and other wide spaces
]

/**
 * Text that Helvetica and Times as built into every PDF reader can draw.
 *
 * Those fonts cover the WinAnsi character set only (basic Latin and Western
 * European letters). jsPDF would write anything else as bytes the font shows
 * as wrong characters. So common typographic characters become their plain
 * equivalents, and whatever is still outside the set becomes "?".
 */
export function pdfSafeText(text: string): string {
  let safe = text
  for (const [pattern, replacement] of PLAIN_EQUIVALENTS) safe = safe.replace(pattern, replacement)
  // Kept: line breaks, printable ASCII, and the Latin-1 letters and signs.
  return safe.replace(/[^\n\x20-\x7E\xA1-\xFF]/g, '?')
}

const FILE_LABEL: Record<DocumentKind, string> = { resume: 'Resume', cover_letter: 'Cover Letter' }

/** "Riley Example - Resume.pdf"; without a usable name just "Resume.pdf". */
export function pdfFileName(generation: Generation, kind: DocumentKind): string {
  const name = (generation.resume?.contact.name ?? '')
    // Not allowed in file names on Windows, macOS or Linux, plus control characters.
    // eslint-disable-next-line no-control-regex
    .replace(/[\\/:*?"<>|\x00-\x1F]/g, '')
    .replace(/\s+/g, ' ')
    // A name must not start or end with a space or a dot.
    .replace(/^[ .]+|[ .]+$/g, '')
  return name ? `${name} - ${FILE_LABEL[kind]}.pdf` : `${FILE_LABEL[kind]}.pdf`
}

// --------------------------------------------------------------- writer

/** One line of text as it was drawn: its left edge, baseline and width in points. */
export interface PdfLine {
  text: string
  x: number
  y: number
  width: number
}

export interface PdfLayout {
  doc: jsPDF
  /** Every line drawn, page by page, in drawing order. */
  pages: PdfLine[][]
}

/**
 * Writes lines from the top of a page downwards and starts a new page when
 * the next line would not fit. Lines are always drawn whole, so a page break
 * never cuts through one.
 */
class PdfWriter {
  readonly doc = new jsPDF({ unit: 'pt', format: 'letter' })
  readonly pages: PdfLine[][] = [[]]
  /** The top of the next line. */
  private y = MARGIN

  private use(style: TextStyle): void {
    this.doc.setFont(style.font, style.weight)
    this.doc.setFontSize(style.size)
  }

  textWidth(text: string, style: TextStyle): number {
    this.use(style)
    return this.doc.getTextWidth(pdfSafeText(text))
  }

  /** `text` as lines no wider than `width`; it is also made safe for the built-in fonts. */
  wrap(text: string, style: TextStyle, width = CONTENT_WIDTH): string[] {
    this.use(style)
    return this.doc.splitTextToSize(pdfSafeText(text), width) as string[]
  }

  /** Start a new page unless `height` more points still fit on this one. */
  keepTogether(height: number): void {
    if (this.y + height <= PAGE_HEIGHT - MARGIN) return
    this.doc.addPage()
    this.pages.push([])
    this.y = MARGIN
  }

  /** Leave a vertical gap. At the top of a page a gap would only waste space. */
  space(points: number): void {
    if (this.y > MARGIN) this.y += points
  }

  private draw(line: string, style: TextStyle, x: number): void {
    this.use(style)
    const baseline = this.y + style.size
    this.doc.text(line, x, baseline)
    this.pages[this.pages.length - 1].push({
      text: line,
      x,
      y: baseline,
      width: this.doc.getTextWidth(line),
    })
  }

  /** Draw already wrapped lines one under the other, with their left edge at `x`. */
  writeLines(lines: string[], style: TextStyle, x = MARGIN): void {
    for (const line of lines) {
      this.keepTogether(lineHeight(style))
      this.draw(line, style, x)
      this.y += lineHeight(style)
    }
  }

  /**
   * Draw one line ending at the right margin, on the row where the next line
   * will start. It does not move down, so that line shares the row.
   */
  writeRightAligned(text: string, style: TextStyle): void {
    const safe = pdfSafeText(text)
    this.draw(safe, style, PAGE_WIDTH - MARGIN - this.textWidth(safe, style))
  }

  /** A bullet: a dot, then the lines with a hanging indent. */
  writeBullet(lines: string[]): void {
    // The dot has to land on the page of the first line.
    this.keepTogether(lineHeight(STYLE.body))
    this.doc.setFillColor(0, 0, 0)
    this.doc.circle(MARGIN + 4, this.y + STYLE.body.size * 0.68, 1.4, 'F')
    this.writeLines(lines, STYLE.body, MARGIN + BULLET_INDENT)
  }

  /** A thin line across the page, as under a section title. */
  rule(): void {
    this.doc.setDrawColor(0, 0, 0)
    this.doc.setLineWidth(0.5)
    this.doc.line(MARGIN, this.y, PAGE_WIDTH - MARGIN, this.y)
  }
}

// --------------------------------------------------------------- layout

/** The name as the heading, then the contact details on one (wrapped) line. */
function writeHeader(writer: PdfWriter, contact: Contact): void {
  if (contact.name) writer.writeLines(writer.wrap(contact.name, STYLE.name), STYLE.name)
  const details = contactDetails(contact).join(' | ')
  if (details) writer.writeLines(writer.wrap(details, STYLE.contact), STYLE.contact)
  writer.space(GAP.afterHeader)
}

/**
 * A section title with a rule under it. `firstBlockHeight` is the height of
 * what follows directly, so the title is never left alone at the bottom of a
 * page.
 */
function writeSectionTitle(writer: PdfWriter, title: string, firstBlockHeight: number): void {
  const lines = writer.wrap(title.toUpperCase(), STYLE.sectionTitle)
  const titleHeight = lines.length * lineHeight(STYLE.sectionTitle) + GAP.afterSectionRule
  writer.space(GAP.beforeSection)
  writer.keepTogether(titleHeight + firstBlockHeight)
  writer.writeLines(lines, STYLE.sectionTitle)
  writer.rule()
  writer.space(GAP.afterSectionRule)
}

/** A section whose content is one paragraph: the summary, the skills. */
function writeParagraphSection(writer: PdfWriter, title: string, text: string): void {
  const lines = writer.wrap(text, STYLE.body)
  // Keep the title with at least the first two lines of the paragraph.
  writeSectionTitle(writer, title, Math.min(lines.length, 2) * lineHeight(STYLE.body))
  writer.writeLines(lines, STYLE.body)
}

/** An entry with its text already wrapped, so its height is known before it is drawn. */
interface WrappedEntry {
  date: string | null
  heading: string[]
  /** Subheading and location, the same line the screen shows under the heading. */
  details: string[]
  bullets: string[][]
}

function wrapEntry(writer: PdfWriter, entry: ResumeEntry): WrappedEntry {
  const date = entry.date_range
  // The heading wraps short of the date, which stands right-aligned on its first line.
  const dateWidth = date ? writer.textWidth(date, STYLE.entryDate) + DATE_GAP : 0
  const details = [entry.subheading, entry.location].filter(Boolean).join(', ')
  return {
    date,
    heading: writer.wrap(entry.heading, STYLE.entryHeading, CONTENT_WIDTH - dateWidth),
    details: details ? writer.wrap(details, STYLE.body) : [],
    bullets: entry.bullets.map((bullet) =>
      writer.wrap(bullet.text, STYLE.body, CONTENT_WIDTH - BULLET_INDENT),
    ),
  }
}

/** The height of an entry's heading, details and first bullet: the part that must share a page. */
function entryStartHeight(entry: WrappedEntry): number {
  const headingHeight = entry.heading.length * lineHeight(STYLE.entryHeading)
  const bodyLines = entry.details.length + (entry.bullets[0]?.length ?? 0)
  return headingHeight + bodyLines * lineHeight(STYLE.body)
}

function writeEntry(writer: PdfWriter, entry: WrappedEntry): void {
  writer.keepTogether(entryStartHeight(entry))
  if (entry.date) writer.writeRightAligned(entry.date, STYLE.entryDate)
  writer.writeLines(entry.heading, STYLE.entryHeading)
  writer.writeLines(entry.details, STYLE.body)
  for (const bullet of entry.bullets) writer.writeBullet(bullet)
}

function writeEntrySection(writer: PdfWriter, section: EntrySection): void {
  const entries = section.entries.map((entry) => wrapEntry(writer, entry))
  writeSectionTitle(writer, section.title, entryStartHeight(entries[0]))
  entries.forEach((entry, index) => {
    if (index > 0) writer.space(GAP.betweenEntries)
    writeEntry(writer, entry)
  })
}

function writeResume(writer: PdfWriter, resume: Resume): void {
  writeHeader(writer, resume.contact)
  if (resume.summary.length > 0) {
    // The summary's statements flow together as one paragraph, as in the copied text.
    writeParagraphSection(writer, 'Summary', resume.summary.map((claim) => claim.text).join(' '))
  }
  for (const section of entrySections(resume)) writeEntrySection(writer, section)
  if (resume.skills.length > 0) {
    writeParagraphSection(writer, 'Skills', resume.skills.map((claim) => claim.text).join(', '))
  }
}

function writeCoverLetter(writer: PdfWriter, letter: CoverLetter, contact: Contact | null): void {
  if (contact) writeHeader(writer, contact)
  for (const paragraph of letter.paragraphs) {
    writer.space(GAP.betweenParagraphs)
    writer.writeLines(writer.wrap(paragraph.text, STYLE.body), STYLE.body)
  }
  // "Sincerely," and the name stay together on one page.
  const signOff = letterSignOff(letter, contact).map((line) => pdfSafeText(line))
  writer.space(GAP.beforeSignOff)
  writer.keepTogether(signOff.length * lineHeight(STYLE.body))
  writer.writeLines(signOff, STYLE.body)
}

/**
 * Lay out one document of the draft. Returns the jsPDF document together
 * with the lines drawn on each page. Throws when the draft has no such
 * document, because an empty file would be worse than an error message.
 */
export function buildPdf(generation: Generation, kind: DocumentKind): PdfLayout {
  const { resume, cover_letter: letter } = generation
  const writer = new PdfWriter()
  if (kind === 'resume') {
    if (!resume) throw new Error('This draft has no resume.')
    writeResume(writer, resume)
  } else {
    if (!letter || letter.paragraphs.length === 0) throw new Error('This draft has no cover letter.')
    writeCoverLetter(writer, letter, resume?.contact ?? null)
  }
  return { doc: writer.doc, pages: writer.pages }
}

/** Build the PDF and hand it to the browser as a file download. */
export function downloadPdf(generation: Generation, kind: DocumentKind): void {
  buildPdf(generation, kind).doc.save(pdfFileName(generation, kind))
}
