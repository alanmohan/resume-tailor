import type { ReactNode } from 'react'
import { FileX } from 'lucide-react'
import { EmptyState } from '@/components/app'
import type { Contact, CoverLetter, Resume, ResumeEntry } from '@/lib/types'
import { ClaimItem } from './ClaimItem'
import { contactDetails, entrySections, letterSignOff } from './workspaceModel'

/**
 * The two documents of a draft, each on a paper-like surface.
 *
 * Names, titles, organisations, dates and contact details are shown exactly
 * as the server composed them from the confirmed profile. All text is passed
 * to React as text nodes, so markup inside it is displayed, never executed.
 *
 * The `ws-*` class names are the hooks used by the print stylesheet
 * (workspace.css); the screen styling is the Tailwind classes next to them.
 */

function Paper({ children }: { children: ReactNode }) {
  return (
    <div className="ws-paper mx-auto w-full max-w-[52rem] rounded-sm border bg-card px-5 py-7 font-serif text-[0.95rem] leading-relaxed text-card-foreground shadow-sm sm:px-10 sm:py-10">
      {children}
    </div>
  )
}

function ContactHeader({ contact }: { contact: Contact }) {
  const details = contactDetails(contact)
  if (!contact.name && details.length === 0) return null
  return (
    <header className="ws-contact border-b pb-4">
      {contact.name ? (
        <h2 className="text-2xl leading-tight font-medium tracking-tight wrap-anywhere sm:text-3xl">
          {contact.name}
        </h2>
      ) : null}
      {details.length > 0 ? (
        <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 font-sans text-sm text-muted-foreground">
          {details.map((detail, index) => (
            <li key={`${index}-${detail}`} className="wrap-anywhere">
              {detail}
            </li>
          ))}
        </ul>
      ) : null}
    </header>
  )
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="mt-6">
      <h3
        id={id}
        className="ws-section-title mb-3 border-b pb-1 font-sans text-xs font-semibold tracking-[0.14em] text-muted-foreground uppercase"
      >
        {title}
      </h3>
      {children}
    </section>
  )
}

/** One confirmed profile record with the statements generated for it. */
function Entry({ entry, regenerable }: { entry: ResumeEntry; regenerable: boolean }) {
  const details = [entry.subheading, entry.location].filter(Boolean).join(', ')
  return (
    <div className="ws-entry">
      <div className="ws-entry-header">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4">
          <h4 className="font-semibold wrap-anywhere">{entry.heading}</h4>
          {entry.date_range ? (
            <p className="font-sans text-sm text-muted-foreground tabular-nums">{entry.date_range}</p>
          ) : null}
        </div>
        {details ? <p className="text-muted-foreground wrap-anywhere">{details}</p> : null}
      </div>
      {entry.bullets.length > 0 ? (
        <ul className="ws-bullets mt-2 list-disc space-y-3 pl-5">
          {entry.bullets.map((bullet) => (
            <ClaimItem key={bullet.item_id} claim={bullet} as="li" regenerable={regenerable} />
          ))}
        </ul>
      ) : null}
      {/* A role or project without statements; a degree or certificate normally has none. */}
      {entry.bullets.length === 0 && regenerable ? (
        <p className="mt-1 font-sans text-xs text-muted-foreground print:hidden">
          No statement was generated for this record, so it appears as a heading only. To change
          that, add detail to it (or remove it) on the Profile step and generate again.
        </p>
      ) : null}
    </div>
  )
}

export function ResumeDocument({ resume }: { resume: Resume | null }) {
  if (!resume) {
    return (
      <EmptyState
        icon={FileX}
        title="This draft has no resume"
        description="Nothing was generated for the resume. Generate a new draft from the target job."
      />
    )
  }
  return (
    <Paper>
      <ContactHeader contact={resume.contact} />

      {resume.summary.length > 0 ? (
        <Section id="resume-summary" title="Summary">
          <div className="ws-summary space-y-3">
            {resume.summary.map((claim) => (
              <ClaimItem key={claim.item_id} claim={claim} />
            ))}
          </div>
        </Section>
      ) : null}

      {entrySections(resume).map((section) => (
        <Section key={section.key} id={`resume-${section.key}`} title={section.title}>
          <div className="space-y-5">
            {section.entries.map((entry) => (
              <Entry key={entry.entry_id} entry={entry} regenerable={section.regenerable} />
            ))}
          </div>
        </Section>
      ))}

      {resume.skills.length > 0 ? (
        <Section id="resume-skills" title="Skills">
          <ul className="ws-skills divide-y divide-border/70 *:py-1.5">
            {resume.skills.map((claim) => (
              <ClaimItem key={claim.item_id} claim={claim} as="li" compact regenerable={false} />
            ))}
          </ul>
        </Section>
      ) : null}
    </Paper>
  )
}

interface CoverLetterDocumentProps {
  coverLetter: CoverLetter | null
  /** The confirmed contact details, used as the letterhead and for the sign-off. */
  contact: Contact | null
}

export function CoverLetterDocument({ coverLetter, contact }: CoverLetterDocumentProps) {
  if (!coverLetter || coverLetter.paragraphs.length === 0) {
    return (
      <EmptyState
        icon={FileX}
        title="This draft has no cover letter"
        description="Nothing was generated for the cover letter. Generate a new draft from the target job."
      />
    )
  }
  const signOff = letterSignOff(coverLetter, contact)
  return (
    <Paper>
      {contact ? <ContactHeader contact={contact} /> : null}
      <div className="ws-letter mt-6 space-y-4">
        {coverLetter.paragraphs.map((claim) => (
          <ClaimItem key={claim.item_id} claim={claim} />
        ))}
      </div>
      {/* Added by the application from confirmed data: plain text, not a statement to validate or edit. */}
      {signOff.length > 0 ? (
        <div className="ws-letter-signoff mt-6">
          {signOff.map((line) => (
            <p key={line} className="wrap-anywhere">
              {line}
            </p>
          ))}
        </div>
      ) : null}
    </Paper>
  )
}
