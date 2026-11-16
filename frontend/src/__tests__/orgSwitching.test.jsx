import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App.jsx'
import Layout from '../components/Layout.jsx'
import OrgPicker from '../pages/OrgPicker.jsx'
import { ToastProvider } from '../components/ui.jsx'
import { api } from '../services/api.js'

vi.mock('../services/api.js', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), del: vi.fn() },
  qs: () => '',
}))

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

const TWO_ORGS = [
  { id: 1, name: 'Personal', kind: 'personal', role: 'owner' },
  { id: 2, name: 'Acme Corp', kind: 'business', role: 'member' },
]

beforeEach(() => {
  vi.clearAllMocks()
})

describe('App org gating (#10)', () => {
  it('shows the workspace picker when /orgs has a session but no current org', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/auth/status') return Promise.resolve({ needs_setup: false })
      if (path === '/api/auth/orgs') return Promise.resolve({ current: null, orgs: TWO_ORGS })
      return Promise.resolve({})
    })

    render(
      <QueryClientProvider client={makeClient()}>
        <MemoryRouter>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('heading', { name: /choose a workspace/i })).toBeInTheDocument()
    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
  })

  it('shows the sign-in form when there is no session at all (401)', async () => {
    api.get.mockImplementation((path) => {
      if (path === '/api/auth/status') return Promise.resolve({ needs_setup: false })
      if (path === '/api/auth/orgs') return Promise.reject(Object.assign(new Error('unauthorized'), { status: 401 }))
      return Promise.resolve({})
    })

    render(
      <QueryClientProvider client={makeClient()}>
        <MemoryRouter>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    // No pending org, no session → the username/password login, not the picker.
    expect(await screen.findByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })
})

describe('OrgPicker (#10)', () => {
  it('calls /select-org for the chosen workspace and invalidates the cache', async () => {
    api.post.mockResolvedValue({ needs_org_selection: false })
    const client = makeClient()
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <OrgPicker orgs={TWO_ORGS} />
        </ToastProvider>
      </QueryClientProvider>,
    )

    await userEvent.click(screen.getByText('Acme Corp'))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/auth/select-org', { org_id: 2 }))
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled())
  })
})

describe('Layout org switcher (#10)', () => {
  function renderLayout(orgsResponse) {
    api.get.mockImplementation((path) => {
      if (path === '/api/auth/orgs') return Promise.resolve(orgsResponse)
      if (path.startsWith('/api/alerts')) return Promise.resolve([])
      return Promise.resolve({})
    })
    const client = makeClient()
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ToastProvider>
            <Layout user={{ username: 'admin', full_name: 'Admin', role: 'admin' }}>
              <div>main content</div>
            </Layout>
          </ToastProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    return client
  }

  it('shows the switcher when the user belongs to more than one org', async () => {
    renderLayout({ current: TWO_ORGS[0], orgs: TWO_ORGS })
    expect(await screen.findByLabelText('Switch organization')).toBeInTheDocument()
  })

  it('hides the switcher for a single-org user (once orgs has resolved)', async () => {
    const client = renderLayout({ current: TWO_ORGS[0], orgs: [TWO_ORGS[0]] })
    await waitFor(() => expect(client.getQueryState(['my-orgs'])?.data).toBeDefined())
    expect(screen.queryByLabelText('Switch organization')).not.toBeInTheDocument()
  })

  it('switches the active org via /select-org and invalidates the cache', async () => {
    api.post.mockResolvedValue({ needs_org_selection: false })
    const client = renderLayout({ current: TWO_ORGS[0], orgs: TWO_ORGS })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const select = await screen.findByLabelText('Switch organization')
    await userEvent.selectOptions(select, '2')

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/auth/select-org', { org_id: 2 }))
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled())
  })
})
