import { useEffect, useRef, type Ref } from 'react'
import { Lightbulb, Quote, Trash2, Undo2, UserPlus } from 'lucide-react'
import { SourceExcerpt, StatusBadge } from '@/components/app'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { Requirement } from '@/lib/types'
import { focusSoon } from './focusSoon'
import {
  CATEGORY_OPTIONS,
  IMPORTANCE_OPTIONS,
  isUserEdited,
  type DraftRequirement,
  type Option,
} from './requirementDraft'

/** The control of a row that can be asked to take keyboard focus. */
export type RowFocusField = 'text' | 'importance' | 'undo'

interface OptionSelectProps<T extends string> {
  id: string
  value: T
  options: Option<T>[]
  onChange: (value: T) => void
  triggerRef?: Ref<HTMLButtonElement>
}

/** A Select over a fixed list of typed options. */
function OptionSelect<T extends string>({
  id,
  value,
  options,
  onChange,
  triggerRef,
}: OptionSelectProps<T>) {
  function handleValueChange(next: string) {
    // The Select reports a plain string; map it back to one of the typed options.
    const option = options.find((item) => item.value === next)
    if (option) onChange(option.value)
  }
  return (
    <Select value={value} onValueChange={handleValueChange}>
      <SelectTrigger id={id} ref={triggerRef} size="sm" className="w-36">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** Marks a requirement that the analysis read between the lines of the posting. */
function InferredBadge() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* A button, so the explanation is also reachable with the keyboard. */}
        <Badge asChild variant="outline">
          <button type="button">
            <Lightbulb aria-hidden="true" />
            Inferred
          </button>
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        Not stated explicitly in the posting. It was inferred from the job description.
      </TooltipContent>
    </Tooltip>
  )
}

interface ViewSourceProps {
  excerpt: string
  position: number
  /** True when the wording was changed, so the excerpt backs the original text. */
  edited: boolean
}

/** Opens the passage of the job description that a requirement was taken from. */
function ViewSource({ excerpt, position, edited }: ViewSourceProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant="link" size="xs" className="h-auto px-0">
          <Quote aria-hidden="true" />
          {edited ? 'View original source' : 'View source'}{' '}
          <span className="sr-only">of requirement {position}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 max-w-[calc(100vw-2rem)] p-3">
        {/* SourceExcerpt renders the untrusted job text as a plain text node. */}
        <SourceExcerpt excerpt={excerpt} label="Job description" />
      </PopoverContent>
    </Popover>
  )
}

interface RequirementFlagsProps {
  requirement: DraftRequirement
  stored: Requirement | undefined
  position: number
}

/** Where a requirement came from: inferred, reworded by the user, added by the user, its source. */
function RequirementFlags({ requirement, stored, position }: RequirementFlagsProps) {
  if (!stored) {
    return (
      <Badge variant="outline">
        <UserPlus aria-hidden="true" />
        Added by you, not saved yet
      </Badge>
    )
  }
  const edited = isUserEdited(requirement, stored)
  if (!stored.inferred && !edited && !stored.source_span) return null
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
      {stored.inferred ? <InferredBadge /> : null}
      {edited ? <StatusBadge status="user_edited" label="User edited" /> : null}
      {stored.source_span ? (
        <ViewSource excerpt={stored.source_span.excerpt} position={position} edited={edited} />
      ) : null}
    </div>
  )
}

interface RequirementRowProps {
  requirement: DraftRequirement
  /** The stored requirement this draft came from; undefined for one added in this session. */
  stored: Requirement | undefined
  /** Number of the requirement in the list, used in labels. */
  position: number
  /** Validation message for the text, shown after a failed save. */
  error: string | undefined
  /**
   * Non-null when one of this row's controls should take keyboard focus (the
   * row was added, moved to the other group, removed or restored). A new
   * object is passed for every request.
   */
  focusRequest: { field: RowFocusField } | null
  onChange: (change: Partial<DraftRequirement>) => void
  onRemove: () => void
  onRestore: () => void
}

/** One editable requirement: wording, category, importance, flags and source. */
export function RequirementRow({
  requirement,
  stored,
  position,
  error,
  focusRequest,
  onChange,
  onRemove,
  onRestore,
}: RequirementRowProps) {
  const textRef = useRef<HTMLTextAreaElement>(null)
  const importanceRef = useRef<HTMLButtonElement>(null)
  const undoRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!focusRequest) return
    const targets = { text: textRef, importance: importanceRef, undo: undoRef }
    return focusSoon(() => targets[focusRequest.field].current)
  }, [focusRequest])

  const id = `requirement-${requirement.key}`

  if (requirement.removed) {
    return (
      <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-dashed px-4 py-3 text-sm">
        <div className="min-w-0 space-y-0.5">
          <p>
            <span className="font-medium">Requirement {position}</span> will be removed when you
            save.
          </p>
          <p className="line-clamp-2 break-words text-muted-foreground">{requirement.text}</p>
        </div>
        <Button
          ref={undoRef}
          type="button"
          variant="outline"
          size="sm"
          aria-label={`Undo removing requirement ${position}`}
          onClick={onRestore}
        >
          <Undo2 aria-hidden="true" />
          Undo
        </Button>
      </li>
    )
  }

  return (
    <li
      role="group"
      aria-label={`Requirement ${position}`}
      className="space-y-3 rounded-xl border p-3 sm:p-4"
    >
      <div className="flex items-start gap-2">
        <Textarea
          ref={textRef}
          rows={2}
          value={requirement.text}
          aria-label={`Requirement ${position} text`}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(event) => onChange({ text: event.target.value })}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={`Remove requirement ${position}`}
          onClick={onRemove}
        >
          <Trash2 aria-hidden="true" />
        </Button>
      </div>
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex items-center gap-2">
          <Label htmlFor={`${id}-category`}>
            Category <span className="sr-only">of requirement {position}</span>
          </Label>
          <OptionSelect
            id={`${id}-category`}
            value={requirement.category}
            options={CATEGORY_OPTIONS}
            onChange={(category) => onChange({ category })}
          />
        </div>
        <div className="flex items-center gap-2">
          <Label htmlFor={`${id}-importance`}>
            Importance <span className="sr-only">of requirement {position}</span>
          </Label>
          <OptionSelect
            id={`${id}-importance`}
            value={requirement.importance}
            options={IMPORTANCE_OPTIONS}
            onChange={(importance) => onChange({ importance })}
            triggerRef={importanceRef}
          />
        </div>
        <RequirementFlags requirement={requirement} stored={stored} position={position} />
      </div>
    </li>
  )
}
