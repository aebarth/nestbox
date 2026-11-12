import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckSquare, FileText, Inbox as InboxIcon, Package, ScanLine, Square } from 'lucide-react'
import { useState } from 'react'
import RecordDrawer from '../components/RecordDrawer.jsx'
import { Badge, EmptyState, Spinner, fmtBytes, fmtDate, useToast } from '../components/ui.jsx'
import { api, qs } from '../services/api.js'

export default function Inbox({ user }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const [selected, setSelected] = useState(new Set())
  const [openRecord, setOpenRecord] = useState(null)
  const [bulkBoxId, setBulkBoxId] = useState('')
  const [bulkProjectId, setBulkProjectId] = useState('')
  const [bulkTypeId, setBulkTypeId] = useState('')

  const inbox = useQuery({
    queryKey: ['records', { status: 'inbox', limit: 100 }],
    queryFn: () => api.get(`/api/records?status=inbox&limit=100`),
  })
  const boxes = useQuery({ queryKey: ['boxes'], queryFn: () => api.get('/api/boxes') })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get('/api/lookups/projects') })
  const types = useQuery({ queryKey: ['record-types'], queryFn: () => api.get('/api/lookups/record-types') })
  const sources = useQuery({ queryKey: ['sources'], queryFn: () => api.get('/api/sources') })

  // Scan now mutation
  const scanMut = useMutation({
    mutationFn: (sourceId) => api.post(`/api/scans${sourceId ? `?source_id=${sourceId}` : ''}`),
    onSuccess: () => {
      toast('Scan queued — check back in a moment for new items.')
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['records'] }), 3000)
    },
  })

  // Bulk patch mutation
  const bulkMut = useMutation({
    mutationFn: ({ ids, patch }) => api.post('/api/records/bulk-patch', { ids, patch }),
    onSuccess: (data) => {
      toast(`Filed ${data.updated} record${data.updated !== 1 ? 's' : ''}${data.errors?.length ? ` (${data.errors.length} skipped)` : ''}`)
      setSelected(new Set())
      setBulkBoxId('')
      setBulkProjectId('')
      setBulkTypeId('')
      queryClient.invalidateQueries({ queryKey: ['records'] })
    },
    onError: (err) => toast(err.detail || 'Bulk action failed', 'error'),
  })

  function toggleSelect(id) {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id); else next.add(id)
    setSelected(next)
  }

  function toggleAll() {
    if (selected.size === inbox.data?.items?.length) setSelected(new Set())
    else setSelected(new Set(inbox.data?.items?.map((r) => r.id)))
  }

  function doBulkPatch() {
    const patch = {}
    if (bulkBoxId) patch.box_id = +bulkBoxId
    if (bulkProjectId) patch.project_id = +bulkProjectId
    if (bulkTypeId) patch.record_type_id = +bulkTypeId
    if (!Object.keys(patch).length) return
    bulkMut.mutate({ ids: [...selected], patch })
  }

  const items = inbox.data?.items || []
  const allSelected = items.length > 0 && selected.size === items.length

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Review Inbox</h1>
        <button className="btn-primary flex items-center gap-2" onClick={() => scanMut.mutate(null)}
                disabled={scanMut.isPending}>
          <ScanLine size={15} /> {scanMut.isPending ? 'Scanning…' : 'Scan all sources'}
        </button>
      </div>

      {/* Bulk actions bar */}
      {selected.size > 0 && (
        <div className="card flex flex-wrap items-center gap-3 border-amber-200 bg-amber-50 p-3">
          <span className="text-sm font-medium text-amber-800">{selected.size} selected</span>
          <select className="input w-40" value={bulkBoxId} onChange={(e) => setBulkBoxId(e.target.value)}>
            <option value="">File into box…</option>
            {boxes.data?.filter((b) => b.status === 'open').map((b) => (
              <option key={b.id} value={b.id}>{b.box_number}</option>
            ))}
          </select>
          <select className="input w-36" value={bulkProjectId} onChange={(e) => setBulkProjectId(e.target.value)}>
            <option value="">Set project…</option>
            {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.code}</option>)}
          </select>
          <select className="input w-36" value={bulkTypeId} onChange={(e) => setBulkTypeId(e.target.value)}>
            <option value="">Set type…</option>
            {types.data?.map((t) => <option key={t.id} value={t.id}>{t.code}</option>)}
          </select>
          <button className="btn-primary" onClick={doBulkPatch} disabled={bulkMut.isPending}>
            {bulkMut.isPending ? 'Applying…' : 'Apply to selected'}
          </button>
        </div>
      )}

      {inbox.isLoading ? (
        <Spinner />
      ) : !items.length ? (
        <div className="card">
          <EmptyState icon={InboxIcon} title="Inbox is empty">
            Run a scan to discover new documents, or all scanned files have been reviewed and filed.
          </EmptyState>
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-slate-200 bg-slate-50">
              <tr>
                <th className="th w-10">
                  <input type="checkbox" checked={allSelected} onChange={toggleAll}
                         className="accent-amber-600" />
                </th>
                <th className="th w-16">Preview</th>
                <th className="th">Document</th>
                <th className="th">Source</th>
                <th className="th">Type / Project</th>
                <th className="th">Size</th>
                <th className="th">Discovered</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((r) => (
                <tr key={r.id}
                    className={`cursor-pointer transition-colors hover:bg-amber-50/40 ${selected.has(r.id) ? 'bg-amber-50/60' : ''}`}
                    onClick={() => setOpenRecord(r.id)}>
                  <td className="td" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(r.id)}
                           onChange={() => toggleSelect(r.id)} className="accent-amber-600" />
                  </td>
                  <td className="td">
                    {r.path?.toLowerCase().endsWith('.pdf') ? (
                      <img src={`/api/records/${r.id}/preview`} alt=""
                           className="h-12 w-auto rounded border border-slate-200 object-contain"
                           onError={(e) => { e.target.style.display = 'none' }} />
                    ) : (
                      <div className="flex h-12 w-9 items-center justify-center rounded bg-slate-100">
                        <FileText size={16} className="text-slate-400" />
                      </div>
                    )}
                  </td>
                  <td className="td max-w-md">
                    <div className="truncate font-medium">{r.title || r.name}</div>
                    <div className="truncate text-xs text-slate-500">{r.path}</div>
                  </td>
                  <td className="td text-sm">{r.source_name || '—'}</td>
                  <td className="td text-sm">
                    {[r.record_type_code, r.project_code].filter(Boolean).join(' / ') || (
                      <span className="text-slate-400">uncategorized</span>
                    )}
                  </td>
                  <td className="td text-xs">{fmtBytes(r.size_bytes)}</td>
                  <td className="td text-xs">{fmtDate(r.first_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="border-t border-slate-200 px-4 py-2.5 text-sm text-slate-600">
            {items.length} document{items.length !== 1 ? 's' : ''} awaiting review
          </div>
        </div>
      )}

      {openRecord && (
        <RecordDrawer recordId={openRecord} isAdmin={user.role === 'admin'}
                      onClose={() => setOpenRecord(null)} />
      )}
    </div>
  )
}
