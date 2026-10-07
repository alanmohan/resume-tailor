import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { useClearData } from '@/lib/hooks'
import { ErrorAlert } from './ErrorAlert'

/**
 * "Clear my data" with an explicit confirmation that lists what is deleted.
 * The dialog stays open while the request runs and when it fails, so the
 * user sees the outcome and can retry.
 */
export function ClearDataDialog() {
  const [open, setOpen] = useState(false)
  const clearData = useClearData()

  function handleOpenChange(nextOpen: boolean) {
    if (clearData.isPending) return
    setOpen(nextOpen)
    if (!nextOpen) clearData.reset()
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogTrigger asChild>
        <Button type="button" variant="ghost" size="sm">
          <Trash2 aria-hidden="true" />
          <span className="sr-only sm:not-sr-only">Clear my data</span>
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete all your data?</AlertDialogTitle>
          <AlertDialogDescription>
            This deletes everything stored for this browser tab right away. It cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>The resume, LinkedIn and notes text you pasted</li>
          <li>Your extracted profile and its evidence index</li>
          <li>Saved job descriptions and their requirements</li>
          <li>Every generated resume and cover letter</li>
        </ul>
        <p className="text-sm text-muted-foreground">
          Your session ends as well, and you return to the Start screen.
        </p>
        {clearData.isError ? (
          <ErrorAlert error={clearData.error} title="Your data was not deleted" />
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={clearData.isPending}>Keep my data</AlertDialogCancel>
          <Button
            type="button"
            variant="destructive"
            disabled={clearData.isPending}
            onClick={() => clearData.mutate()}
          >
            {clearData.isPending
              ? 'Deleting...'
              : clearData.isError
                ? 'Try again'
                : 'Delete everything'}
          </Button>
        </AlertDialogFooter>
        <p role="status" aria-live="polite" className="sr-only">
          {clearData.isPending ? 'Deleting your data' : ''}
        </p>
      </AlertDialogContent>
    </AlertDialog>
  )
}
