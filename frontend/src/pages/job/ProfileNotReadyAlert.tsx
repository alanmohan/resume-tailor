import { Link } from 'react-router'
import { TriangleAlert } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import type { Profile } from '@/lib/types'

/**
 * Shown while the profile is not confirmed and fully indexed. Analyzing and
 * reviewing a job still works; only generation has to wait.
 */
export function ProfileNotReadyAlert({ profile }: { profile: Profile }) {
  const indexing = profile.index_state === 'indexing'
  return (
    <Alert role="status" className="border-warning/40">
      <TriangleAlert aria-hidden="true" className="text-warning" />
      <AlertTitle>
        {indexing ? 'Your profile is still being indexed' : 'Your profile is not confirmed yet'}
      </AlertTitle>
      <AlertDescription>
        {indexing
          ? 'You can analyze a job and review its requirements now. Generating a draft becomes available when indexing has finished; this page updates by itself.'
          : 'Drafts are only generated from a confirmed, fully indexed profile. You can analyze a job and review its requirements now, but generating a draft stays disabled until you confirm your profile.'}
      </AlertDescription>
      {/* Outside AlertDescription, which styles every link inside it as underlined text. */}
      <div className="col-start-2 mt-2">
        <Button asChild size="sm" variant="outline">
          <Link to="/profile">Review and confirm profile</Link>
        </Button>
      </div>
    </Alert>
  )
}
