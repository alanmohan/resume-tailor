/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Public backend address. Never put a secret in a VITE_ variable. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
