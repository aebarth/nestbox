import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import Login from '../pages/Login.jsx'
import { ToastProvider } from '../components/ui.jsx'
import { api } from '../services/api.js'

vi.mock('../services/api.js', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  qs: () => '',
}))

describe('Login', () => {
  it('submits credentials to /api/auth/login', async () => {
    api.post.mockResolvedValue({})
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <Login needsSetup={false} />
        </ToastProvider>
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByLabelText(/username/i), { target: { value: 'admin' } })
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'secret123' } })
    const form = screen.getByRole('button', { name: /sign in/i }).closest('form')
    fireEvent.submit(form)

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/auth/login', { username: 'admin', password: 'secret123' }),
    )
  })
})
