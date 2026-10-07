import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  description?: ReactNode
  /** Buttons aligned with the title on wide screens, below it on narrow ones. */
  actions?: ReactNode
}

/** The page's single h1 with an optional one-paragraph introduction. */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0 space-y-2">
        <h1 className="font-serif text-3xl leading-tight font-medium tracking-tight text-balance sm:text-4xl">
          {title}
        </h1>
        {description ? (
          // wrap-anywhere: a long job title or company without spaces must not widen the page.
          <div className="max-w-prose min-w-0 text-base text-muted-foreground wrap-anywhere">
            {description}
          </div>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  )
}
