import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { newIdempotencyKey, regenerateItem } from '@/lib/api'
import type { Claim } from '@/lib/types'
import { useGenerationWrite } from './generationData'
import { useWorkspace } from './workspaceContext'
import { MAX_INSTRUCTION_CHARS } from './workspaceModel'
import { WriteError } from './WriteError'

interface RegenerateFormProps {
  claim: Claim
  /** Called after a successful regeneration and when the user cancels. */
  onClose: () => void
}

interface Attempt {
  instruction: string
  idempotencyKey: string
}

/**
 * Ask the server to rewrite one statement, optionally with a short
 * instruction. The server writes it from the evidence already retrieved for
 * this draft and validates the result before returning it.
 */
export function RegenerateForm({ claim, onClose }: RegenerateFormProps) {
  const { generation, isWriting } = useWorkspace()
  const [instruction, setInstruction] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const lastAttempt = useRef<Attempt | null>(null)
  const fieldId = `claim-regenerate-${claim.item_id}`

  useEffect(() => {
    input.current?.focus()
  }, [])

  const regenerate = useGenerationWrite({
    generationId: generation.generation_id,
    mutationFn: (attempt: Attempt) =>
      regenerateItem(generation.generation_id, claim.item_id, attempt.idempotencyKey, {
        instruction: attempt.instruction || null,
      }),
    successMessage: 'Statement regenerated',
  })

  /**
   * Retrying the same request reuses its idempotency key, so a first attempt
   * that did reach the server is not paid for twice. A changed instruction
   * is a different request and gets a new key.
   */
  function submit() {
    const wanted = instruction.trim()
    const previous = lastAttempt.current
    const attempt =
      previous?.instruction === wanted
        ? previous
        : { instruction: wanted, idempotencyKey: newIdempotencyKey() }
    lastAttempt.current = attempt
    regenerate.mutate(attempt, { onSuccess: onClose })
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    submit()
  }

  return (
    <form className="mt-2 space-y-2" onSubmit={handleSubmit}>
      <Field>
        <FieldLabel htmlFor={fieldId}>Instruction (optional)</FieldLabel>
        <Input
          id={fieldId}
          ref={input}
          value={instruction}
          maxLength={MAX_INSTRUCTION_CHARS}
          readOnly={regenerate.isPending}
          placeholder="For example: lead with the result"
          aria-describedby={`${fieldId}-help`}
          onChange={(event) => setInstruction(event.target.value)}
        />
        <FieldDescription id={`${fieldId}-help`} className="text-xs">
          Up to {MAX_INSTRUCTION_CHARS} characters. The new wording is written only from the
          evidence retrieved for this draft, then validated. It replaces the current text.
        </FieldDescription>
      </Field>

      {regenerate.isError ? (
        <WriteError
          error={regenerate.error}
          title="This statement was not regenerated"
          onRetry={submit}
          onReloaded={regenerate.reset}
        />
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={isWriting}>
          {regenerate.isPending ? 'Regenerating...' : 'Regenerate'}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={regenerate.isPending}
          onClick={onClose}
        >
          Cancel
        </Button>
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {regenerate.isPending ? 'Regenerating this statement' : ''}
      </p>
    </form>
  )
}
