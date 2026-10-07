import { useState, type MouseEvent } from 'react'
import { Pencil, PencilLine, RefreshCw } from 'lucide-react'
import { cn } from 'cn'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { Claim } from '@/lib/types'
import { ClaimEditor } from './ClaimEditor'
import { ClaimStatusBadge } from './ClaimStatusBadge'
import { EvidenceBadges } from './EvidenceBadges'
import { RegenerateForm } from './RegenerateForm'
import { useWorkspace } from './workspaceContext'
import { useReturnFocus } from './workspaceHooks'
import { claimElementId, countLabel, isFlagged } from './workspaceModel'

type Mode = 'view' | 'edit' | 'regenerate'

interface ClaimItemProps {
  claim: Claim
  /** The element to render: a list item for bullets and skills, otherwise a block. */
  as?: 'li' | 'div'
  /** Put the text and its controls side by side. Used for skills, which are a word or two long. */
  compact?: boolean
  /**
   * Offer Regenerate. False for skills, education and certifications: the
   * server only rewrites summary, experience, project and cover-letter
   * statements, so the button would always end in an error there.
   */
  regenerable?: boolean
}

/**
 * One generated statement.
 *
 * The statement itself is a single text node, which is all that gets printed
 * or copied. Everything about it (evidence badges, validation status,
 * warnings, Edit and Regenerate) lives in a sibling block that is hidden
 * when printing, so review markers can never leak into the document.
 */
export function ClaimItem({
  claim,
  as: Tag = 'div',
  compact = false,
  regenerable = true,
}: ClaimItemProps) {
  const { isWriting } = useWorkspace()
  const [mode, setMode] = useState<Mode>('view')
  const rememberTrigger = useReturnFocus(mode !== 'view')
  const textId = `claim-text-${claim.item_id}`
  const awaitingRevalidation = claim.validation_status === 'user_edited'
  const controlsDisabled = mode !== 'view' || isWriting

  function open(nextMode: Mode, event: MouseEvent<HTMLButtonElement>) {
    rememberTrigger(event.currentTarget)
    setMode(nextMode)
  }

  return (
    <Tag
      id={claimElementId(claim.item_id)}
      data-item-id={claim.item_id}
      tabIndex={-1}
      className={cn(
        'ws-claim rounded-sm focus:outline-2 focus:outline-offset-4 focus:outline-ring',
        compact && 'sm:grid sm:grid-cols-[9rem_minmax(0,1fr)] sm:items-baseline sm:gap-x-3',
      )}
    >
      <p id={textId} className="ws-claim-text break-words whitespace-pre-wrap">
        {claim.text}
      </p>

      <div className={cn('mt-1.5 font-sans text-xs print:hidden', compact && 'sm:mt-0')}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <EvidenceBadges evidenceIds={claim.evidence_ids} subject="statement" />
          <ClaimStatusBadge claim={claim} />
          {claim.user_edited && !awaitingRevalidation ? (
            <Badge variant="outline">
              <PencilLine aria-hidden="true" />
              Edited by you
            </Badge>
          ) : null}
          <span className="inline-flex items-center">
            <Button
              type="button"
              variant="ghost"
              size="xs"
              className="text-muted-foreground"
              aria-label="Edit this statement"
              aria-describedby={textId}
              aria-expanded={mode === 'edit'}
              disabled={controlsDisabled}
              onClick={(event) => open('edit', event)}
            >
              <Pencil aria-hidden="true" />
              Edit
            </Button>
            {regenerable ? (
              <Button
                type="button"
                variant="ghost"
                size="xs"
                className="text-muted-foreground"
                aria-label="Regenerate this statement"
                aria-describedby={textId}
                aria-expanded={mode === 'regenerate'}
                disabled={controlsDisabled}
                onClick={(event) => open('regenerate', event)}
              >
                <RefreshCw aria-hidden="true" />
                Regenerate
              </Button>
            ) : null}
          </span>
        </div>

        {claim.warnings.length > 0 ? (
          // Open from the start when the statement is flagged: the warnings say why.
          <details className="mt-1.5 text-muted-foreground" open={isFlagged(claim)}>
            <summary className="w-fit cursor-pointer select-none">
              {countLabel(claim.warnings.length, 'warning')}
            </summary>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">
              {claim.warnings.map((warning, index) => (
                <li key={`${index}-${warning}`} className="break-words">
                  {warning}
                </li>
              ))}
            </ul>
          </details>
        ) : null}

        {mode === 'edit' ? <ClaimEditor claim={claim} onClose={() => setMode('view')} /> : null}
        {mode === 'regenerate' ? (
          <RegenerateForm claim={claim} onClose={() => setMode('view')} />
        ) : null}
      </div>
    </Tag>
  )
}
