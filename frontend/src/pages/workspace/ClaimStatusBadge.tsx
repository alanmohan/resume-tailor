import { StatusBadge } from '@/components/app'
import type { Claim } from '@/lib/types'

/** Shown instead of "Edited by you" until the statement has been revalidated. */
const AWAITING_REVALIDATION_LABEL = 'Edited - needs revalidation'

/**
 * The validation status of a statement as icon + text. A statement the user
 * edited says that it still needs revalidation; it never shows as supported
 * until the server has checked it again.
 */
export function ClaimStatusBadge({ claim }: { claim: Claim }) {
  const awaitingRevalidation = claim.validation_status === 'user_edited'
  return (
    <StatusBadge
      status={claim.validation_status}
      label={awaitingRevalidation ? AWAITING_REVALIDATION_LABEL : undefined}
    />
  )
}
