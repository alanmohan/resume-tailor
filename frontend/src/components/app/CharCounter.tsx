import { TriangleAlert } from 'lucide-react'
import { cn } from 'cn'

interface CharCounterProps {
  count: number
  /** The limit. Omit it to show a plain count with no limit. */
  max?: number
  /** Lets an input reference the counter with aria-describedby. */
  id?: string
  /** What is being counted, e.g. "characters in total". */
  unit?: string
  className?: string
}

const numberFormat = new Intl.NumberFormat('en-US')

/** "1,234 / 60,000 characters", with an icon and wording (not just colour) when over the limit. */
export function CharCounter({ count, max, id, unit = 'characters', className }: CharCounterProps) {
  const over = max !== undefined && count > max
  return (
    <p
      id={id}
      className={cn(
        'flex flex-wrap items-center gap-x-1.5 text-xs tabular-nums',
        over ? 'font-medium text-destructive' : 'text-muted-foreground',
        className,
      )}
    >
      {over ? <TriangleAlert aria-hidden="true" className="size-3.5" /> : null}
      <span>
        {numberFormat.format(count)}
        {max !== undefined ? ` / ${numberFormat.format(max)}` : ''} {unit}
      </span>
      {over ? <span role="status">({numberFormat.format(count - max)} over the limit)</span> : null}
    </p>
  )
}
