import { ShieldCheck } from 'lucide-react'
import type { ProviderMode } from '@/lib/types'

interface PrivacyNoticeProps {
  /** null while the server's provider is still unknown. */
  providerMode: ProviderMode | null
  /** How long the server keeps session data. */
  ttlHours: number
}

/** What happens to pasted content, shown before anything is submitted. */
export function PrivacyNotice({ providerMode, ttlHours }: PrivacyNoticeProps) {
  return (
    <section aria-labelledby="privacy-heading" className="rounded-xl border bg-muted/40 p-4 sm:p-5">
      <h2 id="privacy-heading" className="flex items-center gap-2 text-base font-medium">
        <ShieldCheck aria-hidden="true" className="size-4 text-primary" />
        How your data is handled
      </h2>
      <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm">
        <li>
          {providerMode === 'fake'
            ? 'This server runs in demo mode: your text is processed by a built-in stand-in, not sent to an AI provider.'
            : 'What you paste is sent to the configured AI provider (OpenAI) to extract your profile and draft your documents.'}
        </li>
        <li>
          This application stores it temporarily, for at most {ttlHours} hours, and then deletes
          it automatically.
        </li>
        <li>
          Access is tied to this browser tab. If you close the tab, you can lose access to your
          work.
        </li>
        <li>
          Once you have started, &ldquo;Clear my data&rdquo; at the top of the page deletes
          everything immediately.
        </li>
      </ul>
    </section>
  )
}
