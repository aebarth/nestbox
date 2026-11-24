import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import Updates from '../pages/Updates.jsx'
import { api } from '../services/api.js'

vi.mock('../services/api.js', () => ({
  api: { get: vi.fn(), post: vi.fn() },
  qs: (params) => {
    const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '' && v !== false)
    return entries.length ? `?${new URLSearchParams(entries).toString()}` : ''
  },
}))

const VERSION = { installed_version: 'v1.0.0', schema_version: 7 }

const CHECK_UPDATE_AVAILABLE = {
  update_available: true,
  latest_version: 'v1.1.0',
  published_at: '2026-11-20T00:00:00Z',
  release_notes: 'Bug fixes and improvements\n- Fixed scanner timeout\n- Added dark mode',
}

const CHECK_UP_TO_DATE = {
  update_available: false,
  latest_version: 'v1.0.0',
  published_at: '2026-11-01T00:00:00Z',
  release_notes: null,
}

const PREPARE_RESULT = {
  backup: { id: 5, filename: 'nestbox-pre-update-v1.1.0.dump' },
  commands: [
    'NESTBOX_VERSION=v1.1.0 docker compose pull',
    'docker compose up -d',
  ],
  pinned_version: 'v1.1.0',
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Updates />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Updates', () => {
  it('shows the current installed version and schema', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/version')) return Promise.resolve(VERSION)
      if (path.startsWith('/api/admin/updates/check')) return Promise.resolve(CHECK_UP_TO_DATE)
      return Promise.resolve(null)
    })

    renderPage()

    expect(await screen.findByText('v1.0.0')).toBeInTheDocument()
    expect(screen.getByText('schema v7')).toBeInTheDocument()
  })

  it('shows "up to date" when no update is available', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/version')) return Promise.resolve(VERSION)
      if (path.startsWith('/api/admin/updates/check')) return Promise.resolve(CHECK_UP_TO_DATE)
      return Promise.resolve(null)
    })

    renderPage()

    expect(await screen.findByText(/You are on the latest version/)).toBeInTheDocument()
  })

  it('shows update available with version and release notes', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/version')) return Promise.resolve(VERSION)
      if (path.startsWith('/api/admin/updates/check')) return Promise.resolve(CHECK_UPDATE_AVAILABLE)
      return Promise.resolve(null)
    })

    renderPage()

    expect(await screen.findByText(/Update available: v1.1.0/)).toBeInTheDocument()
    // Release notes are in a <details> element — expand it
    const user = userEvent.setup()
    await user.click(screen.getByText('Release notes'))
    expect(screen.getByText(/Fixed scanner timeout/)).toBeInTheDocument()
  })

  it('shows the prepare button only when an update is available', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/version')) return Promise.resolve(VERSION)
      if (path.startsWith('/api/admin/updates/check')) return Promise.resolve(CHECK_UPDATE_AVAILABLE)
      return Promise.resolve(null)
    })

    renderPage()

    expect(await screen.findByRole('button', { name: /Prepare update to v1\.1\.0/ })).toBeInTheDocument()
  })

  it('calls prepare and shows docker commands with copy buttons', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/version')) return Promise.resolve(VERSION)
      if (path.startsWith('/api/admin/updates/check')) return Promise.resolve(CHECK_UPDATE_AVAILABLE)
      return Promise.resolve(null)
    })
    api.post.mockResolvedValue(PREPARE_RESULT)

    const user = userEvent.setup()
    renderPage()

    const prepareBtn = await screen.findByRole('button', { name: /Prepare update/ })
    await user.click(prepareBtn)

    expect(await screen.findByText(/Update prepared/)).toBeInTheDocument()

    // Scope to the commands container to avoid matching the rollback <ol> text
    const commandsBox = screen.getByTestId('update-commands')
    expect(within(commandsBox).getByText(PREPARE_RESULT.commands[0])).toBeInTheDocument()
    expect(within(commandsBox).getByText(PREPARE_RESULT.commands[1])).toBeInTheDocument()

    expect(api.post).toHaveBeenCalledWith('/api/admin/updates/prepare')
  })

  it('shows rollback instructions after prepare', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/version')) return Promise.resolve(VERSION)
      if (path.startsWith('/api/admin/updates/check')) return Promise.resolve(CHECK_UPDATE_AVAILABLE)
      return Promise.resolve(null)
    })
    api.post.mockResolvedValue(PREPARE_RESULT)

    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Prepare update/ }))
    expect(await screen.findByText(/If something goes wrong/)).toBeInTheDocument()
  })

  it('verify step confirms the new version after restart', async () => {
    let callCount = 0
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/version')) {
        callCount++
        // First call (initial page load) returns old version;
        // second call (verify click) returns the new version.
        return callCount <= 1
          ? Promise.resolve(VERSION)
          : Promise.resolve({ installed_version: 'v1.1.0', schema_version: 8 })
      }
      if (path.startsWith('/api/admin/updates/check')) return Promise.resolve(CHECK_UPDATE_AVAILABLE)
      return Promise.resolve(null)
    })
    api.post.mockResolvedValue(PREPARE_RESULT)

    const user = userEvent.setup()
    renderPage()

    // Trigger prepare first so the verify section appears
    await user.click(await screen.findByRole('button', { name: /Prepare update/ }))
    await screen.findByText(/Update prepared/)

    // Click "Verify now"
    await user.click(screen.getByRole('button', { name: /Verify now/ }))

    expect(await screen.findByText(/Update verified — running v1\.1\.0/)).toBeInTheDocument()
  })

  it('verify step shows mismatch when version did not change', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/admin/version')) return Promise.resolve(VERSION) // always old
      if (path.startsWith('/api/admin/updates/check')) return Promise.resolve(CHECK_UPDATE_AVAILABLE)
      return Promise.resolve(null)
    })
    api.post.mockResolvedValue(PREPARE_RESULT)

    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Prepare update/ }))
    await screen.findByText(/Update prepared/)

    await user.click(screen.getByRole('button', { name: /Verify now/ }))

    expect(await screen.findByText(/Version mismatch/)).toBeInTheDocument()
  })
})
