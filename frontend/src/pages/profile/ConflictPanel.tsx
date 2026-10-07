import { CircleCheck, CircleSlash, TriangleAlert } from 'lucide-react'
import { SourceExcerpt } from '@/components/app'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { Conflict, ProfileRecord } from '@/lib/types'
import { recordDisplayName, unresolvedConflicts } from './profileDraft'

interface ConflictPanelProps {
  conflicts: Conflict[]
  /** Stored records, used to name the records a conflict affects. */
  records: ProfileRecord[]
  /** Disables the actions while a save is running. */
  busy: boolean
  onResolve: (conflictId: string, resolution: 'resolved' | 'dismissed') => void
}

function affectedRecordNames(conflict: Conflict, records: ProfileRecord[]): string[] {
  return conflict.record_ids
    .map((recordId) => records.find((record) => record.record_id === recordId))
    .filter((record): record is ProfileRecord => record !== undefined)
    .map(recordDisplayName)
}

function ResolutionBadge({ resolution }: { resolution: Conflict['resolution'] }) {
  if (resolution === 'resolved') {
    return (
      <Badge variant="outline">
        <CircleCheck aria-hidden="true" />
        Resolved
      </Badge>
    )
  }
  if (resolution === 'dismissed') {
    return (
      <Badge variant="outline">
        <CircleSlash aria-hidden="true" />
        Not a conflict
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="border-warning/35 bg-warning/10 text-warning">
      <TriangleAlert aria-hidden="true" />
      Unresolved
    </Badge>
  )
}

/**
 * Conflicting facts found across the sources. Nothing is resolved silently:
 * each value is shown with the excerpt it came from, and the user decides.
 */
export function ConflictPanel({ conflicts, records, busy, onResolve }: ConflictPanelProps) {
  if (conflicts.length === 0) return null
  const openCount = unresolvedConflicts(conflicts).length

  return (
    <section
      aria-labelledby="conflicts-heading"
      className="space-y-4 rounded-xl border border-warning/40 bg-warning/5 p-4 sm:p-5"
    >
      <div className="space-y-1">
        <h2 id="conflicts-heading" className="flex items-center gap-2 text-lg font-medium">
          <TriangleAlert aria-hidden="true" className="size-5 text-warning" />
          Conflicts between your sources
        </h2>
        <p className="text-sm text-muted-foreground">
          {openCount > 0
            ? `${openCount} of ${conflicts.length} still need a decision. Correct the affected record below if needed, then mark the conflict resolved, or dismiss it if the values do not actually disagree. Either action also saves your current edits.`
            : 'Every conflict has a decision.'}
        </p>
      </div>

      <ul className="space-y-4">
        {conflicts.map((conflict) => {
          const names = affectedRecordNames(conflict, records)
          return (
            <li key={conflict.conflict_id} className="space-y-3 rounded-lg border bg-background p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-medium">Conflicting {conflict.field}</h3>
                <ResolutionBadge resolution={conflict.resolution} />
              </div>
              <p className="text-sm break-words">{conflict.description}</p>
              {names.length > 0 ? (
                <p className="text-xs text-muted-foreground">Affects: {names.join('; ')}</p>
              ) : null}
              <ul className="space-y-3">
                {conflict.values.map((entry, index) => (
                  <li key={index} className="space-y-1.5">
                    <p className="text-sm font-medium break-words">{entry.value}</p>
                    {entry.source_ref ? (
                      <SourceExcerpt
                        excerpt={entry.source_ref.excerpt}
                        label={entry.source_ref.source_label}
                      />
                    ) : (
                      <p className="text-xs text-muted-foreground">No source excerpt available.</p>
                    )}
                  </li>
                ))}
              </ul>
              {conflict.note ? (
                <p className="text-xs text-muted-foreground">Note: {conflict.note}</p>
              ) : null}
              {conflict.resolution === 'unresolved' ? (
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    disabled={busy}
                    onClick={() => onResolve(conflict.conflict_id, 'resolved')}
                  >
                    Mark resolved
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => onResolve(conflict.conflict_id, 'dismissed')}
                  >
                    Not a conflict
                  </Button>
                </div>
              ) : null}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
