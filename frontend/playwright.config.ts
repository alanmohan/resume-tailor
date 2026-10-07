import { defineConfig, devices } from '@playwright/test'
import {
  BACKEND_DIR,
  BACKEND_PYTHON,
  DROP_E2E_DATABASE,
  FRONTEND_DIR,
} from './e2e/support/backend.ts'
import { API_PORT, API_URL, E2E_DATABASE, MONGODB_URI, WEB_PORT, WEB_URL } from './e2e/support/env.ts'

/**
 * End-to-end tests against isolated local services.
 *
 * Playwright starts both servers itself, on ports that the development
 * servers (8000 and 5173) never use:
 *   - the API on 127.0.0.1:8010 with the deterministic fake AI provider and a
 *     throwaway database that is dropped before and after every run;
 *   - the production build of the frontend on 127.0.0.1:5183, compiled to
 *     talk to that API.
 *
 * Prerequisites: `docker compose up -d mongo` in the project root, and the
 * backend virtual environment at ../backend/.venv (see the setup
 * instructions). No OpenAI call is ever made by these tests.
 */

/** Build output for this run only, so `dist/` keeps the build made for deployment. */
const E2E_BUILD_DIR = 'dist-e2e'

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  globalTeardown: './e2e/support/global-teardown.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : 3,
  // Generous limits: extraction, indexing and generation are real server
  // work, and the suite must also pass on a machine that is busy with other
  // things. A passing run is not slowed down by them.
  timeout: 300_000,
  expect: { timeout: 45_000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: WEB_URL,
    // A missing element fails with its own message, well before the test timeout.
    actionTimeout: 45_000,
    navigationTimeout: 60_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // Reading the clipboard is how the Copy button is checked.
    permissions: ['clipboard-read', 'clipboard-write'],
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      // Start from an empty database, then serve the API with the fake provider.
      command: `"${BACKEND_PYTHON}" -c "${DROP_E2E_DATABASE}" && "${BACKEND_PYTHON}" -m uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: BACKEND_DIR,
      url: `${API_URL}/readyz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        APP_ENV: 'test',
        AI_PROVIDER: 'fake',
        MONGODB_URI,
        MONGODB_DATABASE: E2E_DATABASE,
        CORS_ORIGINS: WEB_URL,
        // Every test opens its own session from the same address; the limits
        // that protect the public deployment would stop the suite half way.
        SESSION_CREATE_LIMIT_PER_HOUR: '100000',
        GLOBAL_DAILY_AI_CALL_LIMIT: '100000',
      },
    },
    {
      command: `npx vite build --outDir ${E2E_BUILD_DIR} --emptyOutDir && npx vite preview --outDir ${E2E_BUILD_DIR} --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      cwd: FRONTEND_DIR,
      url: WEB_URL,
      reuseExistingServer: false,
      timeout: 180_000,
      env: { VITE_API_BASE_URL: API_URL },
    },
  ],
})
