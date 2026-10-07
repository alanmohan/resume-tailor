import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { AppRoutes } from '@/App'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'

/** A QueryClient that never retries, so a mocked failure surfaces immediately. */
function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

function renderWithProviders(ui: ReactElement, route: string) {
  const queryClient = testQueryClient()
  const user = userEvent.setup()
  const view = render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return { ...view, user, queryClient }
}

/** Render the whole application (shell plus routes) at the given path. */
export function renderApp(route = '/') {
  return renderWithProviders(<AppRoutes />, route)
}

/** Render one component with the providers the app's components expect. */
export function renderComponent(ui: ReactElement) {
  return renderWithProviders(ui, '/')
}
