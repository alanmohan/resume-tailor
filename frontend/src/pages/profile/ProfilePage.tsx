import { useIsMutating } from '@tanstack/react-query'
import { Link } from 'react-router'
import { FileText } from 'lucide-react'
import { EmptyState, ErrorAlert, LoadingBlock } from '@/components/app'
import { Button } from '@/components/ui/button'
import { useProfile, useSessionStatus } from '@/lib/hooks'
import { mutationKeys } from '@/lib/queryKeys'
import { ProfileReview } from './ProfileReview'

function NoProfile() {
  return (
    <EmptyState
      icon={FileText}
      title="No profile yet"
      description="Paste your resume, LinkedIn profile or notes on the Start step. The extracted profile appears here for you to review."
      action={
        <Button asChild>
          <Link to="/">Go to Start</Link>
        </Button>
      }
    />
  )
}

/** Route "/profile": loads the session's profile and hands it to the review screen. */
export default function ProfilePage() {
  const status = useSessionStatus()
  // While a confirm request is running, poll so real indexing counts appear.
  const isConfirming = useIsMutating({ mutationKey: mutationKeys.confirmProfile }) > 0
  const query = useProfile({ poll: isConfirming })

  if (status !== 'active') return <NoProfile />
  // Keep the editor mounted whenever a profile is loaded, even if a later
  // background refresh fails, so unsaved edits are never thrown away.
  if (query.data) return <ProfileReview profile={query.data} />
  if (query.isError) {
    return (
      <ErrorAlert
        error={query.error}
        title="Your profile could not be loaded"
        onRetry={() => void query.refetch()}
        isRetrying={query.isFetching}
      />
    )
  }
  if (query.data === null) return <NoProfile />
  return <LoadingBlock label="Loading your profile..." lines={4} />
}
