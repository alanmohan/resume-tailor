import { Clock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

const shortFormat = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})
const longFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'full', timeStyle: 'short' })

interface SessionExpiryHintProps {
  /** ISO timestamp at which the session and its data expire. */
  expiresAt: string
}

/** When the session ends, with a short explanation of the retention rules on demand. */
export function SessionExpiryHint({ expiresAt }: SessionExpiryHintProps) {
  const date = new Date(expiresAt)
  if (Number.isNaN(date.getTime())) return null
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant="ghost" size="sm" className="text-muted-foreground">
          <Clock aria-hidden="true" />
          <span className="sr-only lg:not-sr-only">Expires {shortFormat.format(date)}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-3">
        <p className="font-medium">Session expires {longFormat.format(date)}</p>
        <p className="text-muted-foreground">
          Everything you add is deleted automatically at that time. Access is tied to this
          browser tab, so closing the tab can lose access sooner.
        </p>
      </PopoverContent>
    </Popover>
  )
}
