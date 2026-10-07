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
      <div className="space-y-2">
        <h1 className="font-serif text-3xl leading-tight font-medium tracking-tight text-balance sm:text-4xl">
          {title}
        </h1>
        {description ? (
          <div className="max-w-prose text-base text-muted-foreground">{description}</div>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  )
}
