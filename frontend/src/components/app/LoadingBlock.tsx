import { LoaderCircle } from 'lucide-react'
import { cn } from 'cn'
import { Skeleton } from '@/components/ui/skeleton'

interface LoadingBlockProps {
  /** What is happening, in plain words. Shown and announced to screen readers. */
  label: string
  /** Number of placeholder lines under the label (0 for the label alone). */
  lines?: number
  className?: string
}

const LINE_WIDTHS = ['w-11/12', 'w-4/5', 'w-2/3', 'w-5/6']

/** An honest "working on it" block: a text label plus skeleton lines, no fake percentages. */
export function LoadingBlock({ label, lines = 3, className }: LoadingBlockProps) {
  return (
    <div role="status" aria-live="polite" className={cn('space-y-3', className)}>
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none" />
        {label}
      </p>
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} className={cn('h-4', LINE_WIDTHS[index % LINE_WIDTHS.length])} />
      ))}
    </div>
  )
}
