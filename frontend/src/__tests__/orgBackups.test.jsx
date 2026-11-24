import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import OrgBackups from '../pages/OrgBackups.jsx'
import { ToastProvider } from '../components/ui.jsx'
import { api } from '../services/api.js'

vi.mock('../services/api.js', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  qs: () => '',
}))

const ORGS = [
  { id: 1, name: 'Acme Corp', slug: 'acme', storage: { backups: '/backups/acme' }, health: {}, record_count: 42, file_source_count: 2 },
  { id: 2, name: 'Side Project', slug: 'side', storage: { backups: '/backups/side' }, health: {}, record_count: 0, file_source_count: 1 },
]
const BACKUPS = [
  {
    id: 9, filename: 'acme-org-2026.zip', size_bytes: 20480, sha256: 'ab'.repeat(32),
    kind: 'org', org_id: 1, created_by: 'admin', notes: 'pre-migration',
    created_at: '2026-11-20T12:00:00Z',
  },
]

function renderPage(user) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <OrgBackups user={user} />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockImplementation((path) => {
    if (path === '/api/storage') return Promise.resolve({ install: [], orgs: ORGS })
    if (path.startsWith('/api/storage/org/')) return Promise.resolve(BACKUPS)
    return Promise.resolve([])
  })
  api.post.mockResolvedValue({ id: 10, filename: 'acme-org-2026b.zip' })
})

describe('Org backups page', () => {
  it("lists the selected org's backups and runs a new one with notes", async () => {
    renderPage({ role: 'admin' })
    expect(await screen.findByText('acme-org-2026.zip')).toBeInTheDocument()
    expect(screen.getByText('pre-migration')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Notes (optional)'), { target: { value: 'nightly check' } })
    fireEvent.click(screen.getByRole('button', { name: /run backup/i }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/storage/org/1/backups', { notes: 'nightly check' }),
    )
  })

  it('restores into a chosen target org after confirmation (install admin)', async () => {
    renderPage({ role: 'admin' })
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }))

    const target = await screen.findByLabelText('Target organization')
    fireEvent.change(target, { target: { value: '2' } })

    const confirmBtn = screen.getByRole('button', { name: /restore backup/i })
    expect(confirmBtn).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(confirmBtn)

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/api/storage/org/1/backups/9/restore', { target_org_id: 2, confirm: true },
      ),
    )
    expect(await screen.findByText(/restored into side project/i)).toBeInTheDocument()
  })

  it("org admins restore into their own org with no target picker", async () => {
    // The server returns only the caller's own org to non-install admins.
    api.get.mockImplementation((path) => {
      if (path === '/api/storage') return Promise.resolve({ install: [], orgs: [ORGS[0]] })
      if (path.startsWith('/api/storage/org/')) return Promise.resolve(BACKUPS)
      return Promise.resolve([])
    })

    renderPage({ role: 'user' })
    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }))

    expect(await screen.findByText(/target: the backup.s own organization/i)).toBeInTheDocument()
    expect(screen.queryByLabelText('Target organization')).not.toBeInTheDocument()

    const confirmBtn = screen.getByRole('button', { name: /restore backup/i })
    expect(confirmBtn).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(confirmBtn)

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/storage/org/1/backups/9/restore', { confirm: true }),
    )
  })
})
