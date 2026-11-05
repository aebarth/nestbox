import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import App from '../App.jsx'
import { api } from '../services/api.js'

vi.mock('../services/api.js', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  qs: () => '',
}))

function renderWithProviders(ui) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('App', () => {
  it('shows the setup/login screen when no account exists yet', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/auth/status') return Promise.resolve({ needs_setup: true })
      return Promise.resolve({})
    })

    renderWithProviders(<App />)

    // App gates on /api/auth/status; once it reports needs_setup, the login
    // screen renders with the "create admin account" flow.
    expect(await screen.findByRole('button', { name: /create admin account/i })).toBeInTheDocument()
  })
})
