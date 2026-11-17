import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Inbox from '../pages/Inbox.jsx'
import { ToastProvider } from '../components/ui.jsx'
import { api } from '../services/api.js'

vi.mock('../services/api.js', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  qs: () => '',
}))

const RECORDS = [
  {
    id: 11, title: '', name: 'scan-001.pdf', path: '/sources/scanner/scan-001.pdf',
    status: 'inbox', source_name: 'Scan Inbox', record_type_code: null,
    project_code: null, box_number: null, size_bytes: 51200,
    first_seen_at: '2026-11-17T10:00:00Z',
  },
  {
    id: 12, title: 'Memo', name: 'memo.tif', path: '/sources/scanner/memo.tif',
    status: 'inbox', source_name: 'Scan Inbox', record_type_code: null,
    project_code: null, box_number: null, size_bytes: 8000,
    first_seen_at: '2026-11-17T10:05:00Z',
  },
]

const BOXES = [{ id: 5, box_number: 'B-07', status: 'open' }]

function renderInbox() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <Inbox user={{ role: 'admin' }} />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockImplementation((path) => {
    if (path.startsWith('/api/records')) return Promise.resolve({ total: RECORDS.length, items: RECORDS })
    if (path.startsWith('/api/boxes')) return Promise.resolve(BOXES)
    if (path.startsWith('/api/lookups/projects')) return Promise.resolve([{ id: 1, code: 'P1', name: 'Proj One' }])
    if (path.startsWith('/api/lookups/record-types')) return Promise.resolve([])
    return Promise.resolve([]) // sources
  })
  api.post.mockResolvedValue({})
})

describe('Inbox (review queue)', () => {
  it('renders inbox documents with preview, uncategorized state and count', async () => {
    const { container } = renderInbox()

    expect(await screen.findByText('scan-001.pdf')).toBeInTheDocument()
    expect(screen.getByText('Memo')).toBeInTheDocument()
    // both records are uncategorized
    expect(screen.getAllByText('uncategorized').length).toBe(2)
    expect(screen.getByText('2 documents awaiting review')).toBeInTheDocument()
    // the PDF row gets a rendered preview image; the .tif row falls back to an icon
    expect(container.querySelectorAll('img[src*="/preview"]').length).toBe(1)
  })

  it('"Scan all sources" queues a scan via POST /api/scans', async () => {
    renderInbox()
    await screen.findByText('scan-001.pdf')

    fireEvent.click(screen.getByRole('button', { name: /scan all sources/i }))
    expect(api.post).toHaveBeenCalledWith('/api/scans')
  })

  it('bulk-select then file into a box posts bulk-patch with ids + box_id', async () => {
    api.post.mockImplementation((path) => {
      if (path === '/api/records/bulk-patch') return Promise.resolve({ updated: 2, errors: [] })
      return Promise.resolve({})
    })
    renderInbox()
    await screen.findByText('scan-001.pdf')

    // select-all header checkbox → both rows selected, bulk bar appears
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[0])
    expect(await screen.findByText('2 selected')).toBeInTheDocument()

    // choose the target box in the bulk bar (first combobox is the box picker)
    const selects = screen.getAllByRole('combobox')
    fireEvent.change(selects[0], { target: { value: '5' } })

    fireEvent.click(screen.getByRole('button', { name: /apply to selected/i }))
    expect(api.post).toHaveBeenCalledWith('/api/records/bulk-patch', {
      ids: [11, 12],
      patch: { box_id: 5 },
    })
    // success toast reports how many were filed
    expect(await screen.findByText(/filed 2 records/i)).toBeInTheDocument()
  })
})
