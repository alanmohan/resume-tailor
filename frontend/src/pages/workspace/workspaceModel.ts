/**
 * Pure helpers for the Workspace screen: how a Generation is read as two
 * documents, how its claims and evidence are listed and numbered, and the
 * wording derived from it. No React and no network in this file.
 */
import type {
  Claim,
  Contact,
  CoverageSummary,
  Generation,
  Resume,
  ResumeEntry,
  StaleReason,
  ValidationStatus,
} from '@/lib/types'

export type DocumentKind = 'resume' | 'cover_letter'

export const DOCUMENT_KINDS: DocumentKind[] = ['resume', 'cover_letter']

export const DOCUMENT_LABEL: Record<DocumentKind, string> = {
  resume: 'Resume',
  cover_letter: 'Cover letter',
}

/** Server limits for the inline forms (see backend/app/schemas/generations.py). */
export const MAX_CLAIM_CHARS = 1200
export const MAX_INSTRUCTION_CHARS = 300
export const MAX_NOTE_CHARS = 500

// ------------------------------------------------------------ documents

export interface EntrySection {
  key: 'experience' | 'projects' | 'education' | 'certifications'
  title: string
  entries: ResumeEntry[]
}

/**
 * The resume's record-based sections in display order, without empty ones.
 * The projects section also carries publications and achievements, so its
 * title says so when a publication is present.
 */
export function entrySections(resume: Resume): EntrySection[] {
  const hasPublication = resume.projects.some((entry) => entry.category === 'publication')
  const sections: EntrySection[] = [
    { key: 'experience', title: 'Experience', entries: resume.experience },
    {
      key: 'projects',
      title: hasPublication ? 'Projects and publications' : 'Projects',
      entries: resume.projects,
    },
    { key: 'education', title: 'Education', entries: resume.education },
    { key: 'certifications', title: 'Certifications', entries: resume.certifications },
  ]
  return sections.filter((section) => section.entries.length > 0)
}

function present(values: (string | null)[]): string[] {
  return values.filter((value): value is string => !!value && value.trim() !== '')
}

/** "Software Engineer, Northwind Robotics" - used wherever an entry is named in one line. */
export function entryTitle(entry: ResumeEntry): string {
  return present([entry.heading, entry.subheading]).join(', ')
}

/** Email, phone, location and links, in that order, skipping blanks. */
export function contactDetails(contact: Contact): string[] {
  return present([contact.email, contact.phone, contact.location, ...contact.links])
}

// --------------------------------------------------------------- claims

/** A claim together with where it sits, for jump lists and "cited by". */
export interface LocatedClaim {
  claim: Claim
  documentKind: DocumentKind
  /** Where the claim is in its document, e.g. "Experience: Software Engineer, Northwind Robotics". */
  place: string
}

/** Every claim of the draft in reading order: the resume top to bottom, then the cover letter. */
export function listClaims(generation: Generation): LocatedClaim[] {
  const located: LocatedClaim[] = []
  const add = (documentKind: DocumentKind, place: string, claims: Claim[]) => {
    for (const claim of claims) located.push({ claim, documentKind, place })
  }

  const { resume, cover_letter: coverLetter } = generation
  if (resume) {
    add('resume', 'Summary', resume.summary)
    for (const section of entrySections(resume)) {
      for (const entry of section.entries) {
        add('resume', `${section.title}: ${entryTitle(entry)}`, entry.bullets)
      }
    }
    add('resume', 'Skills', resume.skills)
  }
  coverLetter?.paragraphs.forEach((claim, index) => {
    add('cover_letter', `Paragraph ${index + 1}`, [claim])
  })
  return located
}

const FLAGGED_STATUSES: ValidationStatus[] = ['needs_review', 'unsupported', 'user_edited']

/** A claim the user should look at before exporting: not confirmed by validation, or edited since. */
export function isFlagged(claim: Claim): boolean {
  return FLAGGED_STATUSES.includes(claim.validation_status)
}

/** The DOM id of a claim's container, so other parts of the screen can scroll to and focus it. */
export function claimElementId(itemId: string): string {
  return `claim-${itemId}`
}

// ------------------------------------------------------------- evidence

/**
 * The number shown on each evidence badge.
 *
 * Numbers follow the order of the draft's retrieved context, so a record
 * keeps its number when a statement is edited or regenerated. An ID that is
 * cited but missing from that list (which the server should never produce)
 * still gets a number after the others, so no badge is ever blank.
 */
export function numberEvidence(generation: Generation): Map<string, number> {
  const cited = [
    ...listClaims(generation).flatMap(({ claim }) => claim.evidence_ids),
    ...generation.coverage.flatMap((item) => item.evidence_ids),
  ]
  const numbers = new Map<string, number>()
  for (const evidenceId of [...generation.retrieved_evidence_ids, ...cited]) {
    if (!numbers.has(evidenceId)) numbers.set(evidenceId, numbers.size + 1)
  }
  return numbers
}

/** The heading of an evidence record, matching the number on its badges: "Evidence 3". */
export function evidenceTitle(numbers: ReadonlyMap<string, number>, evidenceId: string): string {
  const number = numbers.get(evidenceId)
  return number === undefined ? 'Evidence' : `Evidence ${number}`
}

// ----------------------------------------------------------- plain text

function entryLines(entry: ResumeEntry): string[] {
  const details = present([entry.location, entry.date_range]).join(' | ')
  return [entryTitle(entry), ...present([details]), ...entry.bullets.map((bullet) => `- ${bullet.text}`)]
}

function resumeBlocks(resume: Resume): string[][] {
  const blocks: string[][] = []
  if (resume.summary.length > 0) {
    blocks.push(['SUMMARY', resume.summary.map((claim) => claim.text).join(' ')])
  }
  for (const section of entrySections(resume)) {
    section.entries.forEach((entry, index) => {
      const lines = entryLines(entry)
      // The section title sits directly above its first entry.
      blocks.push(index === 0 ? [section.title.toUpperCase(), ...lines] : lines)
    })
  }
  if (resume.skills.length > 0) {
    blocks.push(['SKILLS', resume.skills.map((claim) => claim.text).join(', ')])
  }
  return blocks
}

/**
 * One document as plain text for the clipboard: the same content and order as
 * the printed page, with blank lines between blocks and no review markers.
 */
export function documentPlainText(generation: Generation, kind: DocumentKind): string {
  const { resume, cover_letter: coverLetter } = generation
  const blocks: string[][] = []
  if (resume) {
    blocks.push(present([resume.contact.name, contactDetails(resume.contact).join(' | ')]))
  }
  if (kind === 'resume' && resume) blocks.push(...resumeBlocks(resume))
  if (kind === 'cover_letter' && coverLetter) {
    blocks.push(...coverLetter.paragraphs.map((claim) => [claim.text]))
  }
  return blocks
    .filter((lines) => lines.length > 0)
    .map((lines) => lines.join('\n'))
    .join('\n\n')
}

// --------------------------------------------------------------- wording

const percentFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })

/** "62.5%", or "Unavailable" when no requirement could be assessed. */
export function coveragePercentLabel(summary: CoverageSummary): string {
  return summary.percent === null ? 'Unavailable' : `${percentFormat.format(summary.percent)}%`
}

/** Shown for every requirement with status "missing". It never says the person lacks anything. */
export const MISSING_EVIDENCE_TEXT = 'No evidence found in the supplied profile.'

/** One sentence naming why a draft is out of date. */
export function staleExplanation(reasons: StaleReason[]): string {
  const profile = reasons.includes('profile_changed')
  const job = reasons.includes('job_changed')
  let subject = 'Your profile or the target job'
  if (profile && job) subject = 'Your profile and the target job'
  else if (profile) subject = 'Your profile'
  else if (job) subject = 'The target job'
  return `${subject} changed after this draft was generated.`
}

/** "Software Engineer at Northwind Robotics", either part alone, or null when the job has neither. */
export function jobLabel(generation: Generation): string | null {
  const { job_title: title, company } = generation
  if (title && company) return `${title} at ${company}`
  return title || company || null
}

const dateTimeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

/** A timestamp in the reader's own locale and time zone, or null if it cannot be parsed. */
export function formatDateTime(iso: string): string | null {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : dateTimeFormat.format(date)
}

/** "cover_letter" -> "Cover letter": a server section key made readable. */
export function sectionLabel(section: string): string {
  const words = section.replace(/[_-]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** `count` followed by the singular or plural noun: "1 warning", "3 warnings". */
export function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`
}
