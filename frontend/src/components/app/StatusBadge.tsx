import { cn } from 'cn'
import { Badge } from '@/components/ui/badge'
import { STATUS_META, TONE_CLASSES, type Status } from './statusMeta'

interface StatusBadgeProps {
  status: Status
  /** Replace the default label, e.g. "3 need review". The icon stays. */
  label?: string
  className?: string
}

/** A status shown as icon + text label (never colour alone). */
export function StatusBadge({ status, label, className }: StatusBadgeProps) {
  const meta = STATUS_META[status]
  const Icon = meta.icon
  return (
    <Badge
      variant="outline"
      data-status={status}
      className={cn(TONE_CLASSES[meta.tone], className)}
    >
      <Icon aria-hidden="true" />
      {label ?? meta.label}
    </Badge>
  )
}
