import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CharCounter, ErrorAlert, SourceExcerpt, StatusBadge, STATUS_META, type Status } from '@/components/app'
import { ApiError } from '@/lib/errors'

describe('StatusBadge', () => {
  const expectedLabels: Record<Status, string> = {
    supported: 'Supported',
    needs_review: 'Needs review',
    unsupported: 'Unsupported',
    user_edited: 'Edited by you',
    not_applicable: 'No citation needed',
    partial: 'Partially supported',
    missing: 'No evidence found',
    uncertain: 'Uncertain',
  }

  it.each(Object.entries(expectedLabels))('shows a text label and an icon for %s', (status, label) => {
    const { container } = render(<StatusBadge status={status as Status} />)

    expect(screen.getByText(label)).toBeInTheDocument()
    // The icon is decorative; the label carries the meaning.
    expect(container.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })

  it('has metadata for every status and never words coverage as a score', () => {
    for (const meta of Object.values(STATUS_META)) {
      expect(meta.label.trim()).not.toBe('')
      expect(meta.label).not.toMatch(/match|ats|fit|score/i)
    }
  })

  it('accepts a custom label', () => {
    render(<StatusBadge status="needs_review" label="3 items need review" />)

    expect(screen.getByText('3 items need review')).toBeInTheDocument()
  })
})

describe('SourceExcerpt', () => {
  it('renders markup in an excerpt as plain text, not as elements', () => {
    const hostile = '<script>alert(1)</script> and <img src=x onerror="alert(2)"> end'
    const { container } = render(<SourceExcerpt excerpt={hostile} label="Resume" />)

    expect(screen.getByText(hostile)).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[onerror]')).toBeNull()
    expect(screen.getByText('Source: Resume')).toBeInTheDocument()
  })

  it('preserves line breaks with CSS rather than markup', () => {
    render(<SourceExcerpt excerpt={'line one\nline two'} label="Notes" />)

    const quote = screen.getByText(/line one/)
    expect(quote.textContent).toBe('line one\nline two')
    expect(quote).toHaveClass('whitespace-pre-wrap')
  })
})

describe('CharCounter', () => {
  it('shows the count against the limit', () => {
    render(<CharCounter count={1234} max={60000} />)

    expect(screen.getByText('1,234 / 60,000 characters')).toBeInTheDocument()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('says in words when the limit is exceeded', () => {
    render(<CharCounter count={60010} max={60000} />)

    expect(screen.getByRole('status')).toHaveTextContent('(10 over the limit)')
  })
})

describe('ErrorAlert', () => {
  it('shows the message, the request ID and a working Retry button', async () => {
    const onRetry = vi.fn()
    const error = new ApiError({
      code: 'provider_timeout',
      message: 'The AI provider took too long.',
      status: 504,
      requestId: 'req-42',
      retryable: true,
    })
    render(<ErrorAlert error={error} title="Not generated" onRetry={onRetry} />)

    expect(screen.getByRole('alert')).toHaveTextContent('Not generated')
    expect(screen.getByText('The AI provider took too long.')).toBeInTheDocument()
    expect(screen.getByText('req-42')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('lists the fields the API rejected', () => {
    const error = new ApiError({
      code: 'validation_error',
      message: 'Some fields are invalid.',
      status: 422,
      fieldErrors: [{ field: 'records.0.title', message: 'Title is too long.' }],
    })
    render(<ErrorAlert error={error} />)

    expect(screen.getByText('Title is too long.')).toBeInTheDocument()
    expect(screen.getByText('(records.0.title)')).toBeInTheDocument()
  })

  it('falls back to a generic message for unknown errors', () => {
    render(<ErrorAlert error={null} />)

    expect(screen.getByText('Something went wrong. Please try again.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
