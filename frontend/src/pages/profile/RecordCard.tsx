import { useEffect, useRef } from 'react'
import { Plus, Trash2, Undo2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import type { ProfileRecord } from '@/lib/types'
import { focusSoon } from '@/lib/focusSoon'
import { ItemMeta } from './ItemMeta'
import {
  addRecordButtonId,
  newBullet,
  recordDisplayName,
  recordFieldsChanged,
  type CategorySection,
  type DraftBullet,
  type DraftRecord,
} from './profileDraft'
import { SkillsEditor } from './SkillsEditor'

interface RecordCardProps {
  record: DraftRecord
  /** The stored record this draft came from; undefined for a record added in this session. */
  original: ProfileRecord | undefined
  section: CategorySection
  /** Validation message for the title, shown after a failed save. */
  titleError: string | undefined
  /** Move focus to the title when the card first appears (used for newly added records). */
  focusOnMount: boolean
  onChange: (change: Partial<DraftRecord>) => void
  onRemove: () => void
  onRestore: () => void
}

/** One editable profile record: its fields, bullets, skills, provenance and source. */
export function RecordCard({
  record,
  original,
  section,
  titleError,
  focusOnMount,
  onChange,
  onRemove,
  onRestore,
}: RecordCardProps) {
  const titleRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (focusOnMount) titleRef.current?.focus()
  }, [focusOnMount])

  const name = record.title.trim() ? recordDisplayName(record) : `New ${section.singular}`
  const id = `record-${record.key}`
  const isSkillGroup = record.category === 'skill'

  /*
   * Remove and Undo take away the very button that was pressed. Without help
   * keyboard focus would fall back to the top of the page, so each of these
   * actions sends it to the nearest control that still makes sense. The
   * element is looked up by id after React has drawn the new state.
   */
  function handleRemove() {
    onRemove()
    // A stored record leaves an Undo row; an unsaved one is gone, so use the section's Add button.
    focusSoon(
      () =>
        document.getElementById(`${id}-undo`) ??
        document.getElementById(addRecordButtonId(section.category)),
    )
  }

  function handleRestore() {
    onRestore()
    focusSoon(() => document.getElementById(`${id}-title`))
  }

  function removeBullet(index: number) {
    const next = record.bullets.at(index + 1)
    onChange({ bullets: record.bullets.filter((_, position) => position !== index) })
    // The bullet that moves up into the gap, or the Add button after the last one.
    focusSoon(() =>
      document.getElementById(next ? `${id}-bullet-${next.key}` : `${id}-add-bullet`),
    )
  }

  if (record.removed) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-dashed px-4 py-3 text-sm">
        {/* wrap-anywhere: a long title without spaces must not widen the page. */}
        <p className="min-w-0 wrap-anywhere">
          <span className="font-medium">{name}</span> will be removed when you save.
        </p>
        <Button id={`${id}-undo`} type="button" variant="outline" size="sm" onClick={handleRestore}>
          <Undo2 aria-hidden="true" />
          Undo
        </Button>
      </div>
    )
  }

  function changeBullet(key: string, text: string) {
    onChange({
      bullets: record.bullets.map((bullet) => (bullet.key === key ? { ...bullet, text } : bullet)),
    })
  }

  function bulletMeta(bullet: DraftBullet) {
    const stored = original?.bullets.find((item) => item.bullet_id === bullet.bullet_id)
    return (
      <ItemMeta
        provenance={stored?.provenance ?? null}
        changed={!!stored && stored.text.trim() !== bullet.text.trim()}
        needsReview={stored?.needs_review ?? false}
        reviewReasons={stored?.review_reasons ?? []}
        sourceRef={stored?.source_ref ?? null}
      />
    )
  }

  return (
    <Card role="group" aria-label={name}>
      <CardHeader>
        <CardTitle>
          <h3 className="break-words">{name}</h3>
        </CardTitle>
        <CardAction>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Remove ${name}`}
            onClick={handleRemove}
          >
            <Trash2 aria-hidden="true" />
            <span className="hidden sm:inline">Remove</span>
          </Button>
        </CardAction>
        <div className="col-span-full">
          <ItemMeta
            provenance={original?.provenance ?? null}
            changed={!!original && recordFieldsChanged(record, original)}
            needsReview={original?.needs_review ?? false}
            reviewReasons={original?.review_reasons ?? []}
            sourceRef={original?.source_ref ?? null}
          />
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={`${id}-title`}>{section.titleLabel}</Label>
            <Input
              id={`${id}-title`}
              ref={titleRef}
              value={record.title}
              aria-invalid={titleError ? true : undefined}
              aria-describedby={titleError ? `${id}-title-error` : undefined}
              onChange={(event) => onChange({ title: event.target.value })}
            />
            {titleError ? (
              <p id={`${id}-title-error`} role="alert" className="text-sm text-destructive">
                {titleError}
              </p>
            ) : null}
          </div>
          {isSkillGroup ? null : (
            <div className="space-y-1.5">
              <Label htmlFor={`${id}-organization`}>{section.organizationLabel}</Label>
              <Input
                id={`${id}-organization`}
                value={record.organization}
                onChange={(event) => onChange({ organization: event.target.value })}
              />
            </div>
          )}
        </div>

        {isSkillGroup ? null : (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor={`${id}-location`}>Location</Label>
                <Input
                  id={`${id}-location`}
                  value={record.location}
                  onChange={(event) => onChange({ location: event.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${id}-start`}>Start date</Label>
                <Input
                  id={`${id}-start`}
                  value={record.start_date}
                  aria-describedby={`${id}-dates-help`}
                  onChange={(event) => onChange({ start_date: event.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${id}-end`}>End date</Label>
                <Input
                  id={`${id}-end`}
                  value={record.end_date}
                  aria-describedby={`${id}-dates-help`}
                  onChange={(event) => onChange({ end_date: event.target.value })}
                />
              </div>
              <p id={`${id}-dates-help`} className="text-xs text-muted-foreground sm:col-span-3">
                Dates are kept exactly as written and copied unchanged into your documents.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor={`${id}-summary`}>Summary</Label>
              <Textarea
                id={`${id}-summary`}
                rows={2}
                value={record.summary}
                onChange={(event) => onChange({ summary: event.target.value })}
              />
            </div>

            <div className="space-y-3">
              <p className="text-sm font-medium" id={`${id}-bullets`}>
                Bullets
              </p>
              <ul className="space-y-3" aria-labelledby={`${id}-bullets`}>
                {record.bullets.map((bullet, index) => (
                  <li key={bullet.key} className="space-y-1.5">
                    <div className="flex items-start gap-2">
                      <Textarea
                        id={`${id}-bullet-${bullet.key}`}
                        rows={2}
                        value={bullet.text}
                        aria-label={`Bullet ${index + 1} of ${name}`}
                        onChange={(event) => changeBullet(bullet.key, event.target.value)}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={`Remove bullet ${index + 1} of ${name}`}
                        onClick={() => removeBullet(index)}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    </div>
                    {bulletMeta(bullet)}
                  </li>
                ))}
              </ul>
              <Button
                id={`${id}-add-bullet`}
                type="button"
                variant="outline"
                size="sm"
                aria-label={`Add bullet to ${name}`}
                onClick={() => onChange({ bullets: [...record.bullets, newBullet()] })}
              >
                <Plus aria-hidden="true" />
                Add bullet
              </Button>
            </div>
          </>
        )}

        <SkillsEditor
          id={`${id}-skills`}
          label={isSkillGroup ? 'Skills in this group' : 'Skills used'}
          skills={record.skills}
          onChange={(skills) => onChange({ skills })}
        />
      </CardContent>
    </Card>
  )
}
