/**
 * Addresses of the isolated services the end-to-end run starts.
 * They differ from the development ports (8000 and 5173) on purpose, so a
 * test run can never touch a developer's own session or database.
 */
export const API_PORT = 8010
export const WEB_PORT = 5183
export const API_URL = `http://127.0.0.1:${API_PORT}`
export const WEB_URL = `http://127.0.0.1:${WEB_PORT}`

export const MONGODB_URI = 'mongodb://127.0.0.1:27017'
/** APP_ENV=test refuses any database whose name does not start with "resume_tailor_test". */
export const E2E_DATABASE = 'resume_tailor_test_e2e'

/** The single sessionStorage key the app keeps its token under (src/lib/sessionStore.ts). */
export const SESSION_STORAGE_KEY = 'resume-tailor.session'
