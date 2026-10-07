import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import type { DraftContact } from './profileDraft'

interface ContactCardProps {
  contact: DraftContact
  onChange: (change: Partial<DraftContact>) => void
}

const TEXT_FIELDS: { name: 'name' | 'email' | 'phone' | 'location'; label: string; type: string }[] = [
  { name: 'name', label: 'Name', type: 'text' },
  { name: 'email', label: 'Email', type: 'email' },
  { name: 'phone', label: 'Phone', type: 'tel' },
  { name: 'location', label: 'Location', type: 'text' },
]

/** Contact details. Generated documents copy these fields exactly as confirmed here. */
export function ContactCard({ contact, onChange }: ContactCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Contact</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2">
        {TEXT_FIELDS.map((field) => (
          <div key={field.name} className="space-y-1.5">
            <Label htmlFor={`contact-${field.name}`}>{field.label}</Label>
            <Input
              id={`contact-${field.name}`}
              type={field.type}
              autoComplete="off"
              value={contact[field.name]}
              onChange={(event) => onChange({ [field.name]: event.target.value })}
            />
          </div>
        ))}
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="contact-links">Links (one per line)</Label>
          <Textarea
            id="contact-links"
            rows={2}
            value={contact.links}
            onChange={(event) => onChange({ links: event.target.value })}
          />
        </div>
      </CardContent>
    </Card>
  )
}
