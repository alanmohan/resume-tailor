import { Link } from 'react-router'
import { Construction } from 'lucide-react'
import { EmptyState } from '@/components/app'
import { Button } from '@/components/ui/button'

/** Route module for "/job". Minimal stand-in until the Target job screen replaces it. */
export default function JobPage() {
  return (
    <EmptyState
      icon={Construction}
      title="Target job is not available yet"
      description="This screen has not been built into this version of the app."
      action={
        <Button asChild variant="outline">
          <Link to="/profile">Back to profile</Link>
        </Button>
      }
    />
  )
}
