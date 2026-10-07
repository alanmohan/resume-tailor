import { useState } from 'react'
import { Printer } from 'lucide-react'
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

interface PrintGateProps {
  /** The document about to be printed. */
  documentKind: DocumentKind
  /** Flagged statements of the whole draft, in reading order. */
  flagged: LocatedClaim[]
  /** Edits are waiting for revalidation. */
  needsRevalidation: boolean
  /** Close the dialog and go to this statement. */
  onReview: (itemId: string) => void
  /** Close the dialog and print. */
  onPrint: () => void
}

/**
 * The dialog's content. It is mounted only while the dialog is open, so the
 * acknowledgement starts unchecked every time the dialog is opened.
 */
function PrintGate({ documentKind, flagged, needsRevalidation, onReview, onPrint }: PrintGateProps) {
  const [acknowledged, setAcknowledged] = useState(false)
  const documentName = DOCUMENT_LABEL[documentKind].toLowerCase()
  // Statements of the document being printed come first.
  const ordered = [
    ...flagged.filter((item) => item.documentKind === documentKind),
    ...flagged.filter((item) => item.documentKind !== documentKind),
  ]

  return (
    <>
      <DialogHeader>
        <DialogTitle>Review before printing</DialogTitle>
        <DialogDescription>
          {flagged.length > 0
            ? `${countLabel(flagged.length, 'statement has', 'statements have')} not been confirmed by validation. You are about to print the ${documentName}.`
            : `You are about to print the ${documentName}.`}
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
                  aria-describedby={`print-gate-${claim.item_id}`}
                  onClick={() => onReview(claim.item_id)}
                >
                  Review
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                {DOCUMENT_LABEL[claimDocument]} - {place}
              </p>
              <p id={`print-gate-${claim.item_id}`} className="line-clamp-3 font-serif wrap-anywhere">
                {claim.text}
              </p>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="flex items-start gap-2.5">
        <Checkbox
          id="print-gate-acknowledge"
          className="mt-0.5"
          checked={acknowledged}
          onCheckedChange={(checked) => setAcknowledged(checked === true)}
        />
        <Label htmlFor="print-gate-acknowledge" className="leading-snug font-normal">
          I have read these statements and want to print the document as it is.
        </Label>
      </div>

      <DialogFooter>
        <DialogClose asChild>
          <Button type="button" variant="outline">
            Cancel
          </Button>
        </DialogClose>
        <Button type="button" disabled={!acknowledged} onClick={onPrint}>
          <Printer aria-hidden="true" />
          Print anyway
        </Button>
      </DialogFooter>
    </>
  )
}

interface PrintGateDialogProps extends PrintGateProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Radix calls this when the dialog has closed and focus is about to return to the page. */
  onCloseAutoFocus: (event: Event) => void
}

/**
 * Shown instead of printing straight away when statements still need the
 * user's attention: it lists them, offers to go to each one, and only prints
 * after an explicit acknowledgement.
 */
export function PrintGateDialog({ open, onOpenChange, onCloseAutoFocus, ...gate }: PrintGateDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" onCloseAutoFocus={onCloseAutoFocus}>
        <PrintGate {...gate} />
      </DialogContent>
    </Dialog>
  )
}
