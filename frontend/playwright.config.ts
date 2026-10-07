import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end tests against isolated local services.
 *
 * Playwright starts both servers itself, on ports that the development
 * servers (8000 and 5173) never use:
 *   - the API on 127.0.0.1:8010 with the deterministic fake AI provider and a
 *     throwaway database that is dropped before every run;
 *   - the production build of the frontend on 127.0.0.1:5183, compiled to
 *     talk to that API.
 *
 * Prerequisites: `docker compose up -d mongo` in the project root, and the
 * backend virtual environment at ../backend/.venv (see the setup
 * instructions). No OpenAI call is ever made by these tests.
 */

const frontendDir = path.dirname(fileURLToPath(import.meta.url))
const backendDir = path.resolve(frontendDir, '../backend')

const API_PORT = 8010
const WEB_PORT = 5183
export const API_URL = `http://127.0.0.1:${API_PORT}`
export const WEB_URL = `http://127.0.0.1:${WEB_PORT}`

const MONGODB_URI = 'mongodb://127.0.0.1:27017'
/** APP_ENV=test refuses any database whose name does not start with "resume_tailor_test". */
const E2E_DATABASE = 'resume_tailor_test_e2e'
/** Build output for this run only, so `dist/` keeps the build made for deployment. */
const E2E_BUILD_DIR = 'dist-e2e'

const python = path.join(backendDir, '.venv', 'bin', 'python')
const dropDatabase = `from pymongo import MongoClient; MongoClient('${MONGODB_URI}', serverSelectionTimeoutMS=5000).drop_database('${E2E_DATABASE}')`

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : 3,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: WEB_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // Reading the clipboard is how the Copy button is checked.
    permissions: ['clipboard-read', 'clipboard-write'],
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      // Start from an empty database, then serve the API with the fake provider.
      command: `"${python}" -c "${dropDatabase}" && "${python}" -m uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: backendDir,
      url: `${API_URL}/readyz`,
      reuseExistingServer: false,
      timeout: 60_000,
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
      cwd: frontendDir,
      url: WEB_URL,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { VITE_API_BASE_URL: API_URL },
    },
  ],
})
