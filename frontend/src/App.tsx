import { BrowserRouter, Route, Routes } from 'react-router'
import { AppShell } from '@/components/app'
import JobPage from '@/pages/job/JobPage'
import NotFoundPage from '@/pages/NotFoundPage'
import ProfilePage from '@/pages/profile/ProfilePage'
import StartPage from '@/pages/start/StartPage'
import WorkspacePage from '@/pages/workspace/WorkspacePage'

/** The route table, separate from the router so tests can mount it in a MemoryRouter. */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<StartPage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="job" element={<JobPage />} />
        <Route path="workspace/:generationId" element={<WorkspacePage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  )
}
