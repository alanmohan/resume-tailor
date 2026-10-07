import {
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleX,
  Minus,
  PencilLine,
  SearchX,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import type { CoverageStatus, ValidationStatus } from '@/lib/types'

/** Every status the app shows: claim validation plus requirement coverage. */
export type Status = ValidationStatus | CoverageStatus

export type StatusTone = 'success' | 'warning' | 'danger' | 'neutral'

export interface StatusMeta {
  label: string
  icon: LucideIcon
  tone: StatusTone
}

/**
 * Label, icon and tone for each status. A status is never shown by colour
 * alone: the badge always renders the icon and the text label.
 *
 * "missing" is worded as "No evidence found" on purpose: it means nothing in
 * the supplied profile supports the requirement, not that the person lacks it.
 */
export const STATUS_META: Record<Status, StatusMeta> = {
  supported: { label: 'Supported', icon: CircleCheck, tone: 'success' },
  needs_review: { label: 'Needs review', icon: TriangleAlert, tone: 'warning' },
  unsupported: { label: 'Unsupported', icon: CircleX, tone: 'danger' },
  user_edited: { label: 'Edited by you', icon: PencilLine, tone: 'neutral' },
  not_applicable: { label: 'No citation needed', icon: Minus, tone: 'neutral' },
  partial: { label: 'Partially supported', icon: CircleDashed, tone: 'warning' },
  missing: { label: 'No evidence found', icon: SearchX, tone: 'danger' },
  uncertain: { label: 'Uncertain', icon: CircleHelp, tone: 'neutral' },
}

export const TONE_CLASSES: Record<StatusTone, string> = {
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/35 bg-warning/10 text-warning',
  danger: 'border-destructive/30 bg-destructive/10 text-destructive',
  neutral: 'border-border bg-muted text-foreground',
}
