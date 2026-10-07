import { useEffect, useRef, useState, type FormEvent } from 'react'
import { PenLine, UserRoundPen } from 'lucide-react'
import { STATUS_META, StatusBadge } from '@/components/app'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { updateGeneration } from '@/lib/api'
import type { CoverageItem, CoverageStatus } from '@/lib/types'
import { EvidenceBadges } from './EvidenceBadges'
import { useGenerationWrite } from './generationData'
import { useWorkspace } from './workspaceContext'
import { useReturnFocus } from './workspaceHooks'
import { MAX_NOTE_CHARS, MISSING_EVIDENCE_TEXT } from './workspaceModel'
import { WriteError } from './WriteError'

const COVERAGE_STATUSES: CoverageStatus[] = ['supported', 'partial', 'missing', 'uncertain']

const IMPORTANCE_LABEL: Record<string, string> = { required: 'Required', preferred: 'Preferred' }

/**
 * The sentence under a requirement.
 *
 * For "missing" the app always uses its own fixed wording instead of
 * model-written text: the absence of evidence in the supplied profile is all
 * that is known, and the screen must never suggest the person lacks a skill.
 * After a correction by the user, the rationale still describes the
 * automated assessment, so it is labelled as such.
 */
function explanation(item: CoverageItem): string {
  if (item.status === 'missing') {
    return `${MISSING_EVIDENCE_TEXT} If you have this experience, add it to your profile and generate a new draft.`
  }
  return item.user_corrected
    ? `Automated assessment before your correction: ${item.rationale}`
    : item.rationale
}

interface CorrectionFormProps {
  item: CoverageItem
  /** Called after a successful save and when the user cancels. */
  onClose: () => void
}

/** Lets the user overrule the automated status of one requirement, with an optional note. */
function CorrectionForm({ item, onClose }: CorrectionFormProps) {
  const { generation, isWriting } = useWorkspace()
  const [status, setStatus] = useState<CoverageStatus>(item.status)
  const [note, setNote] = useState(item.note ?? '')
  const statusTrigger = useRef<HTMLButtonElement>(null)
  const fieldId = `correction-${item.requirement_id}`

  useEffect(() => {
    statusTrigger.current?.focus()
  }, [])

  const save = useGenerationWrite({
    generationId: generation.generation_id,
    mutationFn: (override: { status: CoverageStatus; note: string | null }) =>
      updateGeneration(generation.generation_id, {
        expected_revision: generation.revision,
        coverage_overrides: [{ requirement_id: item.requirement_id, ...override }],
      }),
    successMessage: 'Correction saved',
  })

  function submit() {
    save.mutate({ status, note: note.trim() || null }, { onSuccess: onClose })
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    submit()
  }

  function handleStatusChange(value: string) {
    const next = COVERAGE_STATUSES.find((candidate) => candidate === value)
    if (next) setStatus(next)
  }

  return (
    <form className="space-y-3 rounded-lg border bg-muted/40 p-3" onSubmit={handleSubmit}>
      <Field>
        <FieldLabel htmlFor={`${fieldId}-status`}>Status</FieldLabel>
        <Select value={status} onValueChange={handleStatusChange} disabled={save.isPending}>
          <SelectTrigger id={`${fieldId}-status`} ref={statusTrigger} className="w-full bg-background">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {COVERAGE_STATUSES.map((option) => (
              <SelectItem key={option} value={option}>
                {STATUS_META[option].label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field>
        <FieldLabel htmlFor={`${fieldId}-note`}>Note (optional)</FieldLabel>
        <Textarea
          id={`${fieldId}-note`}
          className="bg-background"
          rows={2}
          value={note}
          maxLength={MAX_NOTE_CHARS}
          readOnly={save.isPending}
          aria-describedby={`${fieldId}-help`}
          onChange={(event) => setNote(event.target.value)}
        />
        <FieldDescription id={`${fieldId}-help`} className="text-xs">
          A correction records your own assessment and updates the counts. It does not add
          evidence to your profile or change the documents.
        </FieldDescription>
      </Field>

      {save.isError ? (
        <WriteError
          error={save.error}
          title="Your correction was not saved"
          onRetry={submit}
          onReloaded={save.reset}
        />
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={isWriting}>
          {save.isPending ? 'Saving...' : 'Save correction'}
        </Button>
        <Button type="button" variant="outline" size="sm" disabled={save.isPending} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

/** One job requirement: importance, coverage status, why, the evidence, and a way to correct it. */
export function RequirementRow({ item }: { item: CoverageItem }) {
  const { isWriting } = useWorkspace()
  const [correcting, setCorrecting] = useState(false)
  const rememberTrigger = useReturnFocus(correcting)
  const textId = `requirement-${item.requirement_id}`

  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant={item.importance === 'required' ? 'secondary' : 'outline'}>
          {IMPORTANCE_LABEL[item.importance] ?? item.importance}
        </Badge>
        <StatusBadge status={item.status} />
        {item.user_corrected ? (
          <Badge variant="outline">
            <UserRoundPen aria-hidden="true" />
            Corrected by you
          </Badge>
        ) : null}
      </div>

      <p id={textId} className="text-sm font-medium wrap-anywhere">
        {item.requirement_text}
      </p>
      <p className="text-sm text-muted-foreground wrap-anywhere">{explanation(item)}</p>
      {item.note ? <p className="text-sm wrap-anywhere">Your note: {item.note}</p> : null}

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs">
        <EvidenceBadges evidenceIds={item.evidence_ids} subject="requirement" />
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="text-muted-foreground"
          aria-label="Correct this status"
          aria-describedby={textId}
          aria-expanded={correcting}
          disabled={correcting || isWriting}
          onClick={(event) => {
            rememberTrigger(event.currentTarget)
            setCorrecting(true)
          }}
        >
          <PenLine aria-hidden="true" />
          Correct
        </Button>
      </div>

      {correcting ? <CorrectionForm item={item} onClose={() => setCorrecting(false)} /> : null}
    </li>
  )
}
