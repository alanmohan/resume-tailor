import { useEffect, useRef } from 'react'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { Requirement, RequirementImportance } from '@/lib/types'
import { focusSoon } from './focusSoon'
import {
  IMPORTANCE_OPTIONS,
  requirementPositions,
  type DraftRequirement,
} from './requirementDraft'
import { RequirementRow, type RowFocusField } from './RequirementRow'

/**
 * A request to move keyboard focus after the list changed shape: to a control
 * of one row, or to a group's "Add requirement" button. Each request is a new
 * object, so asking for the same control twice in a row still takes effect.
 */
export type FocusTarget =
  | { kind: 'row'; key: string; field: RowFocusField }
  | { kind: 'add'; importance: RequirementImportance }

const EMPTY_GROUP_TEXT: Record<RequirementImportance, string> = {
  required: 'No required qualifications are listed.',
  preferred: 'No preferred qualifications are listed.',
}

interface AddButtonProps {
  groupLabel: string
  disabled: boolean
  /** Non-null when this button should take keyboard focus. */
  focusRequest: FocusTarget | null
  onClick: () => void
}

function AddRequirementButton({ groupLabel, disabled, focusRequest, onClick }: AddButtonProps) {
  const ref = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!focusRequest) return
    return focusSoon(() => ref.current)
  }, [focusRequest])
  return (
    <Button
      ref={ref}
      type="button"
      variant="outline"
      size="sm"
      // Starts with the visible label, and tells the two groups' buttons apart.
      aria-label={`Add requirement to ${groupLabel}`}
      disabled={disabled}
      onClick={onClick}
    >
      <Plus aria-hidden="true" />
      Add requirement
    </Button>
  )
}

interface RequirementListProps {
  requirements: DraftRequirement[]
  /** The requirements as stored on the server, for flags and source excerpts. */
  stored: Requirement[]
  /** Validation messages by requirement key. */
  errors: Record<string, string>
  focusTarget: FocusTarget | null
  /** False once the list has reached the server's limit. */
  canAdd: boolean
  onAdd: (importance: RequirementImportance) => void
  onChange: (key: string, change: Partial<DraftRequirement>) => void
  onRemove: (requirement: DraftRequirement) => void
  onRestore: (key: string) => void
}

/** The requirements in two groups, Required then Preferred, numbered in that order. */
export function RequirementList({
  requirements,
  stored,
  errors,
  focusTarget,
  canAdd,
  onAdd,
  onChange,
  onRemove,
  onRestore,
}: RequirementListProps) {
  const positions = requirementPositions(requirements)

  return (
    <div className="space-y-8">
      {IMPORTANCE_OPTIONS.map((group) => {
        const rows = requirements.filter((requirement) => requirement.importance === group.value)
        const kept = rows.filter((requirement) => !requirement.removed).length
        const headingId = `requirements-${group.value}`
        return (
          <section key={group.value} aria-labelledby={headingId} className="space-y-3">
            <h3 id={headingId} className="text-base font-medium">
              {group.label} <span className="font-normal text-muted-foreground">({kept})</span>
            </h3>
            {rows.length === 0 ? (
              <p className="text-sm text-muted-foreground">{EMPTY_GROUP_TEXT[group.value]}</p>
            ) : (
              <ul className="space-y-3">
                {rows.map((requirement) => (
                  <RequirementRow
                    key={requirement.key}
                    requirement={requirement}
                    stored={stored.find((item) => item.requirement_id === requirement.requirement_id)}
                    position={positions.get(requirement.key) ?? 0}
                    error={errors[requirement.key]}
                    focusRequest={
                      focusTarget?.kind === 'row' && focusTarget.key === requirement.key
                        ? focusTarget
                        : null
                    }
                    onChange={(change) => onChange(requirement.key, change)}
                    onRemove={() => onRemove(requirement)}
                    onRestore={() => onRestore(requirement.key)}
                  />
                ))}
              </ul>
            )}
            <AddRequirementButton
              groupLabel={group.label}
              disabled={!canAdd}
              focusRequest={
                focusTarget?.kind === 'add' && focusTarget.importance === group.value
                  ? focusTarget
                  : null
              }
              onClick={() => onAdd(group.value)}
            />
          </section>
        )
      })}
    </div>
  )
}
