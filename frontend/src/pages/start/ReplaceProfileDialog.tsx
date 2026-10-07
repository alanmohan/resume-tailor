import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'

interface ReplaceProfileDialogProps {
  open: boolean
  /** The user decided to keep the profile they already have. */
  onCancel: () => void
  /** The user agreed to replace it; extraction may start. */
  onConfirm: () => void
  /** Radix calls this when the dialog has closed and focus is about to return to the page. */
  onCloseAutoFocus: (event: Event) => void
}

/**
 * Asked before extracting again in a tab that already has a profile.
 * Extraction replaces the reviewed profile, which cannot be undone, so it
 * needs the same explicit confirmation as "Clear my data".
 */
export function ReplaceProfileDialog({
  open,
  onCancel,
  onConfirm,
  onCloseAutoFocus,
}: ReplaceProfileDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={(nextOpen) => (nextOpen ? undefined : onCancel())}>
      <AlertDialogContent onCloseAutoFocus={onCloseAutoFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle>Replace your profile?</AlertDialogTitle>
          <AlertDialogDescription>
            This tab already has a profile. Extracting again replaces it and cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>Your reviewed profile, including every edit and conflict decision, is replaced</li>
          <li>Its evidence index is discarded, so the new profile must be confirmed again</li>
          <li>Drafts you already generated are marked as out of date</li>
        </ul>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep my profile</AlertDialogCancel>
          <Button type="button" variant="destructive" onClick={onConfirm}>
            Replace my profile
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
