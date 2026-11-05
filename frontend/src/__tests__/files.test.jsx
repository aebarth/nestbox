import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import Files from '../pages/Files.jsx'
import { api } from '../services/api.js'

vi.mock('../services/api.js', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  qs: () => '',
}))

const ITEM = {
  id: 1, title: 'Invoice 1234', name: 'invoice-1234.pdf', source_name: 'Shared Drive',
  path: 'invoices/invoice-1234.pdf', status: 'active', record_type_code: 'INV',
  project_code: 'ACME', lot_number: 'L1', part_number: '', box_number: 'B-01',
  size_bytes: 2048, last_seen_at: '2026-01-02T03:04:05Z', duplicate_group_id: null,
}

describe('Files', () => {
  it('renders records returned by the search API', async () => {
    api.get.mockImplementation((path) => {
      if (path.startsWith('/api/records')) return Promise.resolve({ total: 1, items: [ITEM] })
      return Promise.resolve([]) // sources / lookups
    })

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Files user={{ role: 'admin' }} />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByText('Invoice 1234')).toBeInTheDocument()
    expect(screen.getByText('B-01')).toBeInTheDocument()
  })
})
