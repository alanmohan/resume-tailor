import { Link } from 'react-router'
import { Construction } from 'lucide-react'
import { EmptyState } from '@/components/app'
import { Button } from '@/components/ui/button'

/** Route module for "/workspace/:generationId". Minimal stand-in until the Workspace screen replaces it. */
export default function WorkspacePage() {
  return (
    <EmptyState
      icon={Construction}
      title="Workspace is not available yet"
      description="This screen has not been built into this version of the app."
      action={
        <Button asChild variant="outline">
          <Link to="/profile">Back to profile</Link>
        </Button>
      }
    />
  )
}
