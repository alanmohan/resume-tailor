import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useParams } from 'react-router'
import { toast } from 'sonner'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { Job, Profile } from '@/lib/types'
import {
  errorResponse,
  jsonResponse,
  mockApi,
  seedSession,
  sessionInfo,
  type Routes as ApiRoutes,
} from '@/test/mockApi'
import JobPage from '../JobPage'
import { readyProfile, summaryOf } from './jobFixtures'

/** Stands in for the Workspace screen, so these tests can see where the page navigated to. */
function WorkspaceStub() {
  const { generationId } = useParams()
  return <h1>Workspace {generationId}</h1>
}

/**
 * Mount the Target job route with the app's providers. The other routes are
 * stubs, so these tests exercise this screen only and do not depend on the
 * screens built around it.
 */
export function renderJobPage(route = '/job') {
  // sonner keeps toasts in a module-level store and replays the active ones to
  // every new Toaster, so toasts of earlier tests would show up in this one.
  toast.dismiss()
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const user = userEvent.setup()
  const view = render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route path="/" element={<h1>Start page</h1>} />
            <Route path="/profile" element={<h1>Profile page</h1>} />
            <Route path="/job" element={<JobPage />} />
            <Route path="/workspace/:generationId" element={<WorkspaceStub />} />
          </Routes>
        </MemoryRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return { ...view, user, queryClient }
}

interface OpenOptions {
  /** The session's profile; null means none has been ingested (404). */
  profile?: Profile | null
  /** Jobs the session already has. Each is served by GET /api/jobs/{id}. */
  jobs?: Job[]
  /** Extra or overriding API routes. */
  routes?: ApiRoutes
  route?: string
}

/** Open the Target job screen with a signed-in session and a mocked API. */
export function openJobPage({
  profile = readyProfile(),
  jobs = [],
  routes = {},
  route = '/job',
}: OpenOptions = {}) {
  seedSession()
  const jobRoutes: ApiRoutes = {}
  for (const job of jobs) jobRoutes[`GET /api/jobs/${job.job_id}`] = jsonResponse(job)
  const requests = mockApi({
    'GET /api/session': jsonResponse(sessionInfo({ has_profile: profile !== null })),
    'GET /api/profile': profile
      ? jsonResponse(profile)
      : errorResponse(404, 'not_found', 'No profile'),
    'GET /api/jobs': jsonResponse({ jobs: jobs.map(summaryOf) }),
    ...jobRoutes,
    ...routes,
  })
  return { requests, ...renderJobPage(route) }
}
