import { Button } from '@/components/ui/button'
import { useWorkspace } from './workspaceContext'

interface EvidenceBadgesProps {
  evidenceIds: string[]
  /** What the evidence supports; completes the accessible name of each badge. */
  subject: 'statement' | 'requirement'
}

/**
 * The numbered citation buttons next to a statement or requirement.
 * Selecting one shows that evidence record in the evidence panel. They sit
 * outside the document text and are never printed.
 */
export function EvidenceBadges({ evidenceIds, subject }: EvidenceBadgesProps) {
  const { evidenceNumbers, selectedEvidenceId, selectEvidence } = useWorkspace()
  const uniqueIds = [...new Set(evidenceIds)]
  if (uniqueIds.length === 0) return null

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className="text-muted-foreground">Evidence</span>
      {uniqueIds.map((evidenceId) => {
        const number = evidenceNumbers.get(evidenceId)
        const selected = evidenceId === selectedEvidenceId
        return (
          <Button
            key={evidenceId}
            type="button"
            variant={selected ? 'default' : 'outline'}
            size="icon-xs"
            className="rounded-full tabular-nums"
            aria-pressed={selected}
            aria-label={`Show evidence ${number} for this ${subject}`}
            onClick={() => selectEvidence(evidenceId)}
          >
            {number}
          </Button>
        )
      })}
    </span>
  )
}
