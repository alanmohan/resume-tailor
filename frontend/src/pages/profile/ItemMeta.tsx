import { Quote, UserPlus } from 'lucide-react'
import { SourceExcerpt, StatusBadge } from '@/components/app'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { Provenance, SourceRef } from '@/lib/types'

interface ViewSourceProps {
  sourceRef: SourceRef
  /** True when the user changed the text, so the excerpt is the original wording. */
  edited: boolean
}

/** Opens the exact excerpt an extracted item was taken from, with its source label. */
export function ViewSource({ sourceRef, edited }: ViewSourceProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant="link" size="xs" className="h-auto px-0">
          <Quote aria-hidden="true" />
          {edited ? 'View original source' : 'View source'}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 max-w-[calc(100vw-2rem)] p-3">
        <SourceExcerpt excerpt={sourceRef.excerpt} label={sourceRef.source_label} />
      </PopoverContent>
    </Popover>
  )
}

interface ItemMetaProps {
  /** Provenance stored on the server, or null for an item that is not saved yet. */
  provenance: Provenance | null
  /** The user changed this item since it was loaded. */
  changed: boolean
  needsReview: boolean
  reviewReasons: string[]
  sourceRef: SourceRef | null
}

/**
 * Where an item came from and whether it needs attention: needs-review badge
 * with its reasons, "Edited by you" / "Added by you", and the source control.
 */
export function ItemMeta({ provenance, changed, needsReview, reviewReasons, sourceRef }: ItemMetaProps) {
  const edited = changed || provenance === 'user_edited'
  const added = provenance === 'user_added' || provenance === null
  if (!needsReview && !edited && !added && !sourceRef) return null
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
      {needsReview ? <StatusBadge status="needs_review" /> : null}
      {needsReview && reviewReasons.length > 0 ? (
        <span className="text-warning">{reviewReasons.join('; ')}</span>
      ) : null}
      {added ? (
        <Badge variant="outline">
          <UserPlus aria-hidden="true" />
          {provenance === null ? 'Added by you, not saved yet' : 'Added by you'}
        </Badge>
      ) : edited ? (
        <StatusBadge status="user_edited" />
      ) : null}
      {sourceRef ? <ViewSource sourceRef={sourceRef} edited={edited} /> : null}
    </div>
  )
}
