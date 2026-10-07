import { useState, type KeyboardEvent } from 'react'
import { Plus, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface SkillsEditorProps {
  /** Unique prefix for element ids. */
  id: string
  label: string
  skills: string[]
  onChange: (skills: string[]) => void
}

/** Split typed or pasted text on commas into trimmed, non-empty skills. */
function parseSkills(text: string): string[] {
  return text
    .split(',')
    .map((skill) => skill.trim())
    .filter((skill) => skill !== '')
}

/** Skills as removable chips, plus an input that accepts one skill or a comma-separated list. */
export function SkillsEditor({ id, label, skills, onChange }: SkillsEditorProps) {
  const [pending, setPending] = useState('')

  function addPending() {
    const existing = new Set(skills.map((skill) => skill.toLowerCase()))
    const additions = parseSkills(pending).filter((skill) => !existing.has(skill.toLowerCase()))
    if (additions.length > 0) onChange([...skills, ...additions])
    setPending('')
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter') return
    // Enter adds the skill instead of submitting a surrounding form.
    event.preventDefault()
    addPending()
  }

  return (
    <div className="space-y-2">
      <Label htmlFor={`${id}-input`}>{label}</Label>
      {skills.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label={`${label}: current list`}>
          {skills.map((skill, index) => (
            <li key={`${skill}-${index}`}>
              <Badge variant="secondary" className="h-auto max-w-full gap-0.5 py-0.5 pr-0.5 whitespace-normal">
                <span className="break-words">{skill}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  className="size-5 rounded-full"
                  aria-label={`Remove ${skill}`}
                  onClick={() => onChange(skills.filter((_, position) => position !== index))}
                >
                  <X aria-hidden="true" />
                </Button>
              </Badge>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex gap-2">
        <Input
          id={`${id}-input`}
          value={pending}
          placeholder="Add a skill, or several separated by commas"
          autoComplete="off"
          onChange={(event) => setPending(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        <Button type="button" variant="outline" onClick={addPending} disabled={pending.trim() === ''}>
          <Plus aria-hidden="true" />
          Add
        </Button>
      </div>
    </div>
  )
}
