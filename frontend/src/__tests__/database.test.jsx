import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import Database from '../pages/Database.jsx'
import { api } from '../services/api.js'

vi.mock('../services/api.js', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  qs: () => '',
}))

const BACKUP = {
  id: 1,
  filename: 'nestbox-20261106-030000-ab12cd34.dump',
  size_bytes: 20480,
  sha256: 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789',
  kind: 'scheduled',
  created_by: 1,
  notes: '',
  created_at: '2026-11-06T03:00:00Z',
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Database />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Database', () => {
  it('renders the backup list with checksum, size and kind', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/backups')) return Promise.resolve([BACKUP])
      return Promise.resolve([])
    })

    renderPage()

    expect(await screen.findByText(BACKUP.filename)).toBeInTheDocument()
    expect(screen.getByText('sha256 abcdef0123456789…')).toBeInTheDocument()
    expect(screen.getByText('20.0 KB')).toBeInTheDocument()
    expect(screen.getByText('scheduled')).toBeInTheDocument()
  })

  it('shows an empty state when there are no backups', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/backups')) return Promise.resolve([])
      return Promise.resolve([])
    })

    renderPage()

    expect(await screen.findByText('No backups yet.')).toBeInTheDocument()
  })

  it('keeps restore disabled until the exact confirmation word is typed', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/backups')) return Promise.resolve([BACKUP])
      return Promise.resolve([])
    })
    api.post.mockResolvedValue({ ok: true })

    const user = userEvent.setup()
    renderPage()
    await screen.findByText(BACKUP.filename)

    await user.click(screen.getByText('Restore'))
    const input = screen.getByPlaceholderText('type RESTORE')
    const confirm = screen.getByRole('button', { name: /confirm/i })

    expect(confirm).toBeDisabled()
    await user.type(input, 'restore') // wrong case
    expect(confirm).toBeDisabled()
    await user.clear(input)
    await user.type(input, 'RESTORE')
    expect(confirm).toBeEnabled()

    await user.click(confirm)
    expect(api.post).toHaveBeenCalledWith('/api/admin/backups/1/restore', { confirm: 'RESTORE' })
  })
})
