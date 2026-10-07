import { execFileSync } from 'node:child_process'
import { BACKEND_PYTHON, DROP_E2E_DATABASE } from './backend.ts'

/** Leave nothing behind: drop the throwaway database once every test has finished. */
export default function globalTeardown(): void {
  execFileSync(BACKEND_PYTHON, ['-c', DROP_E2E_DATABASE], { stdio: 'inherit' })
}
