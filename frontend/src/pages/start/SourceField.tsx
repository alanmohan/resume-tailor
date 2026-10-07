import { useFormContext, useWatch } from 'react-hook-form'
import { CharCounter } from '@/components/app'
import { Field, FieldDescription, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { SOURCE_SLOTS, type StartFormValues } from './startForm'

interface SourceFieldProps {
  /** Position in SOURCE_SLOTS and in the form's `sources` array. */
  index: number
  disabled: boolean
}

/** One paste area: an editable source label, the text, and a live character count. */
export function SourceField({ index, disabled }: SourceFieldProps) {
  const slot = SOURCE_SLOTS[index]
  const { register, formState } = useFormContext<StartFormValues>()
  const text = useWatch<StartFormValues, `sources.${number}.text`>({
    name: `sources.${index}.text`,
  })
  const errors = formState.errors.sources?.[index]
  const labelId = `source-${slot.source_type}-label`
  const textId = `source-${slot.source_type}-text`
  const counterId = `${textId}-count`

  return (
    <fieldset className="min-w-0 space-y-4 rounded-xl border p-4 sm:p-5" disabled={disabled}>
      <legend className="px-1.5 text-base font-medium">{slot.heading}</legend>

      <Field data-invalid={errors?.text ? true : undefined}>
        <FieldLabel htmlFor={textId}>{slot.heading} text</FieldLabel>
        <Textarea
          id={textId}
          rows={6}
          placeholder={slot.placeholder}
          className="max-h-80 min-h-32"
          aria-invalid={errors?.text ? true : undefined}
          aria-describedby={counterId}
          {...register(`sources.${index}.text`)}
        />
        <CharCounter id={counterId} count={text.length} />
        <FieldError errors={[errors?.text]} />
      </Field>

      <Field data-invalid={errors?.label ? true : undefined}>
        <FieldLabel htmlFor={labelId}>{slot.heading} source label</FieldLabel>
        <Input
          id={labelId}
          autoComplete="off"
          aria-invalid={errors?.label ? true : undefined}
          aria-describedby={`${labelId}-help`}
          {...register(`sources.${index}.label`)}
        />
        <FieldDescription id={`${labelId}-help`}>
          Shown next to every excerpt quoted from this text.
        </FieldDescription>
        <FieldError errors={[errors?.label]} />
      </Field>
    </fieldset>
  )
}
