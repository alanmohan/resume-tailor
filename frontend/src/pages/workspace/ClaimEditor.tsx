import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CharCounter } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { Textarea } from '@/components/ui/textarea'
import { updateGeneration } from '@/lib/api'
import type { Claim } from '@/lib/types'
import { useGenerationWrite } from './generationData'
import { useWorkspace } from './workspaceContext'
import { MAX_CLAIM_CHARS } from './workspaceModel'
import { WriteError } from './WriteError'

interface ClaimEditorProps {
  claim: Claim
  /** Called after a successful save and when the user cancels. */
  onClose: () => void
}

/**
 * Inline editor for one statement.
 *
 * Saving sends only this statement's new text together with the revision the
 * page is showing. The server never rewrites the text; it marks the
 * statement as edited by the user until it has been revalidated.
 */
export function ClaimEditor({ claim, onClose }: ClaimEditorProps) {
  const { generation, isWriting } = useWorkspace()
  const [text, setText] = useState(claim.text)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const fieldId = `claim-edit-${claim.item_id}`

  // Start typing at the end of the existing text.
  useEffect(() => {
    const element = textarea.current
    if (!element) return
    element.focus()
    element.setSelectionRange(element.value.length, element.value.length)
  }, [])

  const save = useGenerationWrite({
    generationId: generation.generation_id,
    mutationFn: (nextText: string) =>
      updateGeneration(generation.generation_id, {
        expected_revision: generation.revision,
        edits: [{ item_id: claim.item_id, text: nextText }],
      }),
    successMessage: 'Edit saved. Revalidate before you export.',
  })

  const trimmed = text.trim()
  let problem: string | null = null
  if (trimmed === '') problem = 'Enter the statement, or cancel to keep it as it is.'
  else if (text.length > MAX_CLAIM_CHARS) problem = `Shorten this to ${MAX_CLAIM_CHARS} characters or fewer.`
  const unchanged = trimmed === claim.text.trim()

  function submit() {
    if (!problem && !unchanged) save.mutate(trimmed, { onSuccess: onClose })
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    submit()
  }

  return (
    <form className="mt-2 space-y-2" onSubmit={handleSubmit} noValidate>
      <Field data-invalid={problem ? true : undefined}>
        <FieldLabel htmlFor={fieldId}>Edit statement</FieldLabel>
        <Textarea
          id={fieldId}
          ref={textarea}
          value={text}
          rows={3}
          readOnly={save.isPending}
          aria-invalid={problem ? true : undefined}
          aria-describedby={`${fieldId}-count ${fieldId}-error`}
          onChange={(event) => setText(event.target.value)}
        />
        <CharCounter id={`${fieldId}-count`} count={text.length} max={MAX_CLAIM_CHARS} />
        <FieldError id={`${fieldId}-error`}>{problem}</FieldError>
      </Field>

      {save.isError ? (
        <WriteError
          error={save.error}
          title="Your edit was not saved"
          onRetry={submit}
          onReloaded={save.reset}
        />
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={isWriting || !!problem || unchanged}>
          {save.isPending ? 'Saving...' : 'Save'}
        </Button>
        <Button type="button" variant="outline" size="sm" disabled={save.isPending} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
