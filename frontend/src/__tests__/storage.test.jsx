import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Storage from '../pages/Storage.jsx'
import { ToastProvider } from '../components/ui.jsx'
import { api } from '../services/api.js'

vi.mock('../services/api.js', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  qs: () => '',
}))

const OK = { path: '/x', exists: true, readable: true, writable: true, free_bytes: 5 * 1024 ** 3 }
const STORAGE = {
  install: [
    { location: 'inbox', container_path: '/inbox', host_path: '/host/inbox', health: OK },
    { location: 'sources', container_path: '/sources', host_path: null, health: OK },
    { location: 'backups', container_path: '/backups', host_path: null, health: OK },
  ],
  orgs: [
    {
      id: 1, name: 'Acme Corp', slug: 'acme',
      storage: { inbox: '/inbox/acme', sources: '/sources/acme', backups: '/backups/acme' },
      health: { inbox: OK, sources: OK, backups: OK },
      record_count: 42, file_source_count: 2,
    },
    {
      id: 2, name: 'Side Project', slug: 'side',
      storage: { inbox: '/inbox/side', sources: '/sources/side', backups: '/backups/side' },
      health: { inbox: OK, sources: OK, backups: OK },
      record_count: 0, file_source_count: 1,
    },
  ],
}

function renderPage(user) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <Storage user={user} />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockImplementation((path) =>
    path === '/api/storage' ? Promise.resolve(STORAGE) : Promise.resolve([]),
  )
  api.put.mockResolvedValue({ ok: true, warnings: [] })
  api.post.mockResolvedValue({ ok: true })
})

describe('Admin → Storage', () => {
  it("shows install mounts and the selected org's locations", async () => {
    renderPage({ role: 'admin' })
    expect(await screen.findByText('/inbox/acme')).toBeInTheDocument()
    expect(screen.getByText('/host/inbox')).toBeInTheDocument()

    // Switching orgs follows through to the location paths.
    fireEvent.change(screen.getByLabelText('Organization'), { target: { value: '2' } })
    expect(await screen.findByText('/sources/side')).toBeInTheDocument()
  })

  it('probes a location with the writability test', async () => {
    renderPage({ role: 'admin' })
    await screen.findByText('/inbox/acme')

    // One Test button per row, in inbox / sources / backups order.
    fireEvent.click(screen.getAllByRole('button', { name: 'Test' })[0])
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/storage/test', { location: 'inbox', org_id: 1 }),
    )
    expect(await screen.findByText(/inbox is writable/i)).toBeInTheDocument()
  })

  it('requires confirmation to change the sources root of an org with records, sending only changed fields', async () => {
    renderPage({ role: 'admin' })
    await screen.findByText('/inbox/acme')
    fireEvent.click(screen.getByRole('button', { name: /change locations/i }))

    // Modal inputs in order: inbox, sources, backups.
    const inputs = Array.from(document.querySelectorAll('input.input'))
    fireEvent.change(inputs[1], { target: { value: '/sources/acme/records' } })

    const save = screen.getByRole('button', { name: /save locations/i })
    expect(save).toBeDisabled()
    expect(await screen.findByText(/will NOT move existing files/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('checkbox'))
    expect(save).toBeEnabled()
    fireEvent.click(save)

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith('/api/storage/org/1', {
        sources_root: '/sources/acme/records',
        confirm_sources_change: true,
      }),
    )
  })
})
