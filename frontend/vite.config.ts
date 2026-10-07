import path from 'node:path'
import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const rootDir = path.dirname(fileURLToPath(import.meta.url))

// One config for the dev server, the production build and the unit tests.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(rootDir, './src'),
    },
  },
  server: {
    // Fixed origin so the backend can allow it exactly in CORS_ORIGINS.
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    restoreMocks: true,
    // Many tests render the whole app and wait for mocked network round
    // trips. The default 5 s is too tight when the machine is busy (several
    // test files run in parallel); a passing test is not slowed down by this.
    // The matching limit for one findBy.../waitFor is set in src/test/setup.ts.
    testTimeout: 20_000,
    // Each worker runs a full browser-like environment. With one worker per
    // CPU core the files starve each other on a busy machine and tests time
    // out in a different file on every run; three at a time stayed stable.
    maxWorkers: 3,
  },
})
