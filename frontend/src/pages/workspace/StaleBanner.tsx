import { Link } from 'react-router'
import { TriangleAlert } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import type { StaleReason } from '@/lib/types'
import { jobPath } from '@/pages/job/jobRoutes'
import { staleExplanation } from './workspaceModel'

/**
 * Marks a draft that no longer matches its inputs and says which input
 * changed. The draft stays readable, copyable and downloadable; a current one
 * has to be generated again from the Target job step.
 */
export function StaleBanner({ reasons, jobId }: { reasons: StaleReason[]; jobId: string }) {
  return (
    <Alert role="status" className="border-warning/40 print:hidden">
      <TriangleAlert aria-hidden="true" className="text-warning" />
      <AlertTitle>This draft is out of date</AlertTitle>
      <AlertDescription>
        {staleExplanation(reasons)} You can still read, copy and download it, but it does not
        reflect that change.
      </AlertDescription>
      {/* Outside AlertDescription, which styles every link inside it as underlined text. */}
      <div className="col-start-2 mt-2">
        <Button asChild size="sm">
          {/* The draft's own job, which is already analyzed; the bare "/job" is the form for a new one. */}
          <Link to={jobPath(jobId)}>Generate a new draft</Link>
        </Button>
      </div>
    </Alert>
  )
}
