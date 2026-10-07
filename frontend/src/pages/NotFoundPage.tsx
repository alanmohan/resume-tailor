import { Link } from 'react-router'
import { FileQuestion } from 'lucide-react'
import { EmptyState } from '@/components/app'
import { Button } from '@/components/ui/button'

export default function NotFoundPage() {
  return (
    <EmptyState
      icon={FileQuestion}
      title="Page not found"
      description="This address does not match any screen in Resume Tailor."
      action={
        <Button asChild>
          <Link to="/">Go to Start</Link>
        </Button>
      }
    />
  )
}
