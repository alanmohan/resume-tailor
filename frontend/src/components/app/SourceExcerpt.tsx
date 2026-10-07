import { cn } from 'cn'

interface SourceExcerptProps {
  /** Exact text from the user's source. Rendered as plain text only. */
  excerpt: string
  /** Where the text came from, e.g. the source label "Resume". */
  label: string
  /** Optional second line, e.g. the role or project the excerpt belongs to. */
  detail?: string
  className?: string
}

/**
 * A quoted source excerpt with its label.
 *
 * The excerpt is untrusted text, so it is only ever passed to React as a
 * text node (which escapes it). Whitespace and line breaks are preserved
 * with CSS, not by generating markup.
 */
export function SourceExcerpt({ excerpt, label, detail, className }: SourceExcerptProps) {
  return (
    <figure className={cn('border-l-2 border-primary/60 pl-3', className)}>
      <blockquote className="font-serif text-[0.95rem] leading-relaxed break-words whitespace-pre-wrap text-foreground">
        {excerpt}
      </blockquote>
      <figcaption className="mt-1.5 text-xs text-muted-foreground">
        <span>Source: {label}</span>
        {detail ? <span className="block">{detail}</span> : null}
      </figcaption>
    </figure>
  )
}
