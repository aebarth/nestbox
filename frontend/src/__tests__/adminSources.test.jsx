import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Admin from '../pages/Admin.jsx'
import { ToastProvider } from '../components/ui.jsx'
import { api } from '../services/api.js'

vi.mock('../services/api.js', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  qs: () => '',
}))

const SOURCES = [
  {
    id: 7, name: 'Scanner Out', kind: 'local', config: { root: '/sources/scanner' },
    is_monitored: true, scan_cron: null, is_active: true,
    has_credentials: false, last_scan_at: null,
  },
]

function renderAdmin() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <Admin />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function openSourcesTab() {
  renderAdmin()
  fireEvent.click(await screen.findByRole('button', { name: 'Sources' }))
  return screen.findByText('Scanner Out')
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockImplementation((path) => {
    if (path.startsWith('/api/sources')) return Promise.resolve(SOURCES)
    return Promise.resolve([]) // users / lookups / settings / scans / audit
  })
  api.post.mockResolvedValue({})
})

describe('Admin → Sources', () => {
  it('shows a readable source and reports a successful connection test', async () => {
    api.post.mockResolvedValue({ ok: true, file_count: 3 })
    await openSourcesTab()

    fireEvent.click(screen.getByRole('button', { name: 'Test' }))
    expect(api.post).toHaveBeenCalledWith('/api/sources/7/test')
    expect(await screen.findByText(/scanner out looks good/i)).toBeInTheDocument()
  })

  it('shows the server error detail when a connection test fails', async () => {
    api.post.mockResolvedValue({ ok: false, detail: 'No such file or directory' })
    await openSourcesTab()

    fireEvent.click(screen.getByRole('button', { name: 'Test' }))
    expect(await screen.findByText('No such file or directory')).toBeInTheDocument()
  })

  it('add-source form submits a local/network-share source with its root', async () => {
    await openSourcesTab()

    fireEvent.click(screen.getByRole('button', { name: /add source/i }))
    // the modal's inputs render synchronously: Name, Folder (placeholder), cron
    const inputs = Array.from(document.querySelectorAll('input.input'))
    fireEvent.change(inputs[0], { target: { value: 'Canon R50' } })
    fireEvent.change(screen.getByPlaceholderText('/sources/…'), { target: { value: '/sources/canon-r50' } })

    const save = screen.getByRole('button', { name: /save source/i })
    expect(save).toBeEnabled()
    fireEvent.click(save)

    // React Query defers mutationFn to a microtask after mutate(), so wait for it
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/sources', expect.objectContaining({
        name: 'Canon R50',
        kind: 'local',
        config: { root: '/sources/canon-r50' },
        is_monitored: true,
        is_active: true,
      })),
    )
  })
})
