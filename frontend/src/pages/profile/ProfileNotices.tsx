import { Info } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import type { ProfileNotice } from '@/lib/types'

/**
 * What the extraction wants the user to know about its own result, such as
 * "No name or contact details were found" or source lines it did not capture.
 * It informs only: confirming the profile stays possible. The messages are
 * shown as plain text.
 */
export function ProfileNotices({ notices }: { notices: ProfileNotice[] }) {
  if (notices.length === 0) return null
  return (
    <Alert role="status">
      <Info aria-hidden="true" />
      <AlertTitle>Notes from extraction</AlertTitle>
      <AlertDescription>
        <ul className="mb-2 list-disc space-y-1 pl-4">
          {notices.map((notice, index) => (
            <li key={`${index}-${notice.code}`} className="wrap-anywhere">
              {notice.message}
            </li>
          ))}
        </ul>
        <p>
          These do not stop you from confirming. Check the records below and add anything that
          is missing.
        </p>
      </AlertDescription>
    </Alert>
  )
}
