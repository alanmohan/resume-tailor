import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { createQueryClient } from '@/lib/queryClient'
import { applyTheme } from '@/lib/theme'
import App from './App'
import './index.css'

const queryClient = createQueryClient()

// Set the light/dark class on <html> before the first paint.
applyTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <App />
        {/* Top right, below the sticky header, so toasts never cover a page's action bar. */}
        <Toaster position="top-right" offset={{ top: 64 }} mobileOffset={{ top: 104 }} />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
