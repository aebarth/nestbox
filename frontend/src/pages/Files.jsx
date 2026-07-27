import { useQuery } from '@tanstack/react-query'
import { FileText, Search } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import RecordDrawer from '../components/RecordDrawer.jsx'
import { Badge, EmptyState, Spinner, StatusBadge, fmtBytes, fmtDate } from '../components/ui.jsx'
import { api, qs } from '../services/api.js'

export default function Files({ user }) {
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(params.get('q') || '')
  const [openRecord, setOpenRecord] = useState(params.get('record') ? +params.get('record') : null)
  const page = +(params.get('page') || 0)

  const filters = {
    q: params.get('q') || '',
    status: params.get('status') || '',
    source_id: params.get('source_id') || '',
    project_id: params.get('project_id') || '',
    record_type_id: params.get('record_type_id') || '',
    box_id: params.get('box_id') || '',
    duplicates_only: params.get('duplicates_only') === 'true',
    limit: 50,
    offset: page * 50,
  }

  const records = useQuery({
    queryKey: ['records', filters],
    queryFn: () => api.get(`/api/records${qs(filters)}`),
    keepPreviousData: true,
  })
  const sources = useQuery({ queryKey: ['sources'], queryFn: () => api.get('/api/sources') })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get('/api/lookups/projects') })
  const types = useQuery({ queryKey: ['record-types'], queryFn: () => api.get('/api/lookups/record-types') })

  function setFilter(key, value) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    next.delete('page')
    setParams(next)
  }

  const data = records.data
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">Files</h1>

      <div className="card flex flex-wrap items-center gap-2 p-3">
        <form className="relative min-w-64 flex-1" onSubmit={(e) => { e.preventDefault(); setFilter('q', q) }}>
          <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
          <input className="input pl-9" placeholder="Search name, title, path, lot, part number, or document contents…"
                 value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
        <select className="input w-40" value={filters.status} onChange={(e) => setFilter('status', e.target.value)}>
          <option value="">Any status</option>
          {['active', 'inbox', 'missing', 'retired', 'superseded'].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select className="input w-44" value={filters.source_id} onChange={(e) => setFilter('source_id', e.target.value)}>
          <option value="">Any source</option>
          {sources.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select className="input w-40" value={filters.project_id} onChange={(e) => setFilter('project_id', e.target.value)}>
          <option value="">Any project</option>
          {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.code}</option>)}
        </select>
        <select className="input w-40" value={filters.record_type_id} onChange={(e) => setFilter('record_type_id', e.target.value)}>
          <option value="">Any type</option>
          {types.data?.map((t) => <option key={t.id} value={t.id}>{t.code}</option>)}
        </select>
        <label className="flex items-center gap-1.5 px-2 text-sm text-slate-600">
          <input type="checkbox" checked={filters.duplicates_only}
                 onChange={(e) => setFilter('duplicates_only', e.target.checked ? 'true' : '')} />
          Duplicates
        </label>
      </div>

      {records.isLoading ? (
        <Spinner />
      ) : !data?.items?.length ? (
        <div className="card">
          <EmptyState icon={FileText} title="No records match">
            Adjust the filters, or run a scan from the Dashboard to import files.
          </EmptyState>
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-slate-200 bg-slate-50">
              <tr>
                <th className="th">Record</th>
                <th className="th">Status</th>
                <th className="th">Type</th>
                <th className="th">Project</th>
                <th className="th">Lot / Part</th>
                <th className="th">Box</th>
                <th className="th">Size</th>
                <th className="th">Last seen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.items.map((r) => (
                <tr key={r.id} className="cursor-pointer hover:bg-amber-50/40"
                    onClick={() => setOpenRecord(r.id)}>
                  <td className="td max-w-md">
                    <div className="truncate font-medium">{r.title || r.name}</div>
                    <div className="truncate text-xs text-slate-500">{r.source_name} · {r.path}</div>
                  </td>
                  <td className="td">
                    <div className="flex gap-1">
                      <StatusBadge status={r.status} />
                      {r.duplicate_group_id && <Badge tone="amber">dup</Badge>}
                    </div>
                  </td>
                  <td className="td">{r.record_type_code || '—'}</td>
                  <td className="td">{r.project_code || '—'}</td>
                  <td className="td text-xs">{[r.lot_number, r.part_number].filter(Boolean).join(' / ') || '—'}</td>
                  <td className="td">{r.box_number || '—'}</td>
                  <td className="td text-xs">{fmtBytes(r.size_bytes)}</td>
                  <td className="td text-xs">{fmtDate(r.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-between border-t border-slate-200 px-4 py-2.5 text-sm text-slate-600">
            <span>{data.total} records</span>
            <div className="flex gap-2">
              <button className="btn-secondary" disabled={page === 0}
                      onClick={() => setFilter('page', String(page - 1))}>Previous</button>
              <button className="btn-secondary" disabled={(page + 1) * 50 >= data.total}
                      onClick={() => setFilter('page', String(page + 1))}>Next</button>
            </div>
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
