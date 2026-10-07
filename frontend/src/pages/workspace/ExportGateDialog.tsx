import { useState } from 'react'
import { Copy, Printer, type LucideIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { ClaimStatusBadge } from './ClaimStatusBadge'
import { DOCUMENT_LABEL, countLabel, type DocumentKind, type LocatedClaim } from './workspaceModel'

/** The two ways a document leaves the app. Both go through the same review. */
export type ExportAction = 'print' | 'copy'

const ACTION_WORDING: Record<ExportAction, { title: string; verb: string; confirm: string; icon: LucideIcon }> = {
  print: { title: 'Review before printing', verb: 'print', confirm: 'Print anyway', icon: Printer },
  copy: { title: 'Review before copying', verb: 'copy', confirm: 'Copy anyway', icon: Copy },
}

interface ExportGateProps {
  /** What the user asked for when the dialog opened. */
  action: ExportAction
  /** The document about to be printed or copied. */
  documentKind: DocumentKind
  /** Flagged statements of the whole draft, in reading order. */
  flagged: LocatedClaim[]
  /** Edits are waiting for revalidation. */
  needsRevalidation: boolean
  /** Close the dialog and go to this statement. */
  onReview: (itemId: string) => void
  /** Close the dialog and carry out the action. */
  onConfirm: () => void
}

/**
 * The dialog's content. It is mounted only while the dialog is open, so the
 * acknowledgement starts unchecked every time the dialog is opened.
 */
function ExportGate({
  action,
  documentKind,
  flagged,
  needsRevalidation,
  onReview,
  onConfirm,
}: ExportGateProps) {
  const [acknowledged, setAcknowledged] = useState(false)
  const { title, verb, confirm, icon: ConfirmIcon } = ACTION_WORDING[action]
  const documentName = DOCUMENT_LABEL[documentKind].toLowerCase()
  // Statements of the document being exported come first.
  const ordered = [
    ...flagged.filter((item) => item.documentKind === documentKind),
    ...flagged.filter((item) => item.documentKind !== documentKind),
  ]

  return (
    <>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>
          {flagged.length > 0
            ? `${countLabel(flagged.length, 'statement has', 'statements have')} not been confirmed by validation. You are about to ${verb} the ${documentName}.`
            : `You are about to ${verb} the ${documentName}.`}
        </DialogDescription>
      </DialogHeader>

      {needsRevalidation ? (
        <p className="text-sm">
          Some statements were edited after the last validation. Close this dialog and choose
          Revalidate to check them against your evidence.
        </p>
      ) : null}

      {ordered.length > 0 ? (
        <ul className="max-h-64 space-y-3 overflow-y-auto pr-1">
          {ordered.map(({ claim, documentKind: claimDocument, place }) => (
            <li key={claim.item_id} className="space-y-1.5 rounded-lg border p-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <ClaimStatusBadge claim={claim} />
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  aria-describedby={`export-gate-${claim.item_id}`}
                  onClick={() => onReview(claim.item_id)}
                >
                  Review
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                {DOCUMENT_LABEL[claimDocument]} - {place}
              </p>
              <p id={`export-gate-${claim.item_id}`} className="line-clamp-3 font-serif wrap-anywhere">
                {claim.text}
              </p>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="flex items-start gap-2.5">
        <Checkbox
          id="export-gate-acknowledge"
          className="mt-0.5"
          checked={acknowledged}
          onCheckedChange={(checked) => setAcknowledged(checked === true)}
        />
        <Label htmlFor="export-gate-acknowledge" className="leading-snug font-normal">
          I have read these statements and want to {verb} the document as it is.
        </Label>
      </div>

      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="outline">
            Cancel
          </Button>
        </DialogClose>
        <Button type="button" disabled={!acknowledged} onClick={onConfirm}>
          <ConfirmIcon aria-hidden="true" />
          {confirm}
        </Button>
      </DialogFooter>
    </>
  )
}

interface ExportGateDialogProps extends ExportGateProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Radix calls this when the dialog has closed and focus is about to return to the page. */
  onCloseAutoFocus: (event: Event) => void
}

/**
 * Shown instead of printing or copying straight away when statements still
 * need the user's attention: it lists them, offers to go to each one, and
 * only exports after an explicit acknowledgement.
 */
export function ExportGateDialog({ open, onOpenChange, onCloseAutoFocus, ...gate }: ExportGateDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" onCloseAutoFocus={onCloseAutoFocus}>
        <ExportGate {...gate} />
      </DialogContent>
    </Dialog>
  )
}
