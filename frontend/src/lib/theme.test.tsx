import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { ThemeToggle } from '@/components/app'
import { THEME_STORAGE_KEY, applyTheme, getTheme, setTheme } from '@/lib/theme'

afterEach(() => {
  document.documentElement.classList.remove('dark')
})

describe('theme', () => {
  it('defaults to the system preference and applies a saved choice as a class on <html>', () => {
    expect(getTheme()).toBe('light')

    setTheme('dark')

    expect(document.documentElement).toHaveClass('dark')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')
    expect(getTheme()).toBe('dark')
  })

  it('restores the saved theme on load', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark')

    applyTheme()

    expect(document.documentElement).toHaveClass('dark')
  })

  it('toggles from a labelled button', async () => {
    render(<ThemeToggle />)

    await userEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }))

    expect(document.documentElement).toHaveClass('dark')
    expect(screen.getByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument()
  })
})
