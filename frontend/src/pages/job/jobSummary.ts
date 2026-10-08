/** "Title at Company" for headings, links and screen-reader labels. */
export function jobDisplayName(job: { title: string | null; company: string | null }): string {
  const title = job.title?.trim()
  const company = job.company?.trim()
  if (title && company) return `${title} at ${company}`
  return title || company || 'Untitled job'
}

const dateTimeFormat = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
})

/** An API timestamp in the reader's own locale and time zone. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : dateTimeFormat.format(date)
}
