import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, Printer, ScanText, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../services/api.js'
import PdfViewer from './PdfViewer.jsx'
import { Badge, Field, StatusBadge, fmtBytes, fmtDate, useToast } from './ui.jsx'

function TagInput({ value, onChange }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const t = draft.trim()
    if (t && !value.includes(t)) onChange([...value, t])
    setDraft('')
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2 py-1.5">
      {value.map((t) => (
        <Badge key={t} tone="amber">
          {t}
          <button className="ml-1" onClick={() => onChange(value.filter((x) => x !== t))}>×</button>
        </Badge>
      ))}
      <input
        className="min-w-[80px] flex-1 border-none bg-transparent p-0.5 text-sm focus:outline-none"
        value={draft}
        placeholder="add tag…"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
        onBlur={add}
      />
    </div>
  )
}

function OcrPanel({ recordId }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const text = useQuery({
    queryKey: ['record-text', recordId],
    queryFn: () => api.get(`/api/records/${recordId}/text`),
    refetchInterval: (query) => (query.state.data?.ocr_status === 'pending' ? 10_000 : false),
  })
  const rerun = useMutation({
    mutationFn: () => api.post(`/api/records/${recordId}/ocr`),
    onSuccess: (r) => {
      toast(r.detail)
      queryClient.invalidateQueries({ queryKey: ['record-text', recordId] })
    },
  })
  const t = text.data
  if (!t) return null
  const statusTone = { done: 'green', pending: 'blue', failed: 'red', skipped: 'slate' }[t.ocr_status]
  return (
    <div className="mt-6">
      <div className="mb-2 flex items-center gap-2">
        <ScanText size={15} className="text-slate-400" />
        <h3 className="text-sm font-semibold">Document text</h3>
        <Badge tone={statusTone}>{t.ocr_status}</Badge>
        {t.ocr_method && t.ocr_status === 'done' && (
          <span className="text-xs text-slate-400">
            {t.ocr_method === 'tesseract' ? 'OCR' : 'embedded text'} · {t.chars.toLocaleString()} chars
          </span>
        )}
        <button className="ml-auto text-xs text-amber-600 hover:underline"
                onClick={() => rerun.mutate()} disabled={rerun.isPending || t.ocr_status === 'pending'}>
          Re-extract
        </button>
      </div>
      {t.ocr_status === 'pending' && (
        <p className="text-xs text-slate-500">Queued — text extraction runs in the background.</p>
      )}
      {t.ocr_status === 'failed' && (
        <p className="text-xs text-rose-600">Extraction failed: {t.ocr_method}</p>
      )}
      {t.ocr_status === 'done' && t.chars > 0 && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
          <p className={expanded ? 'whitespace-pre-wrap' : 'line-clamp-3 whitespace-pre-wrap'}>
            {t.preview}
          </p>
          <button className="mt-1 text-amber-600 hover:underline"
                  onClick={() => setExpanded(!expanded)}>
            {expanded ? 'Show less' : 'Show more'}
          </button>
        </div>
      )}
    </div>
  )
}

export default function RecordDrawer({ recordId, onClose, isAdmin }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const [viewing, setViewing] = useState(false)
  const record = useQuery({
    queryKey: ['record', recordId],
    queryFn: () => api.get(`/api/records/${recordId}`),
    enabled: !!recordId,
  })
  const events = useQuery({
    queryKey: ['record-events', recordId],
    queryFn: () => api.get(`/api/records/${recordId}/events`),
    enabled: !!recordId,
  })
  const dupes = useQuery({
    queryKey: ['record-dupes', recordId],
    queryFn: () => api.get(`/api/records/${recordId}/duplicates`),
    enabled: !!recordId,
  })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get('/api/lookups/projects') })
  const types = useQuery({ queryKey: ['record-types'], queryFn: () => api.get('/api/lookups/record-types') })
  const boxes = useQuery({ queryKey: ['boxes'], queryFn: () => api.get('/api/boxes') })

  const [form, setForm] = useState(null)
  useEffect(() => {
    if (record.data) {
      const r = record.data
      setForm({
        title: r.title, project_id: r.project_id, record_type_id: r.record_type_id,
        lot_number: r.lot_number, part_number: r.part_number, tags: r.tags,
        notes: r.notes, box_id: r.box_id,
      })
    }
  }, [record.data])

  const save = useMutation({
    mutationFn: (body) => api.patch(`/api/records/${recordId}`, body),
    onSuccess: () => {
      toast('Record saved')
      queryClient.invalidateQueries({ queryKey: ['record', recordId] })
      queryClient.invalidateQueries({ queryKey: ['records'] })
      queryClient.invalidateQueries({ queryKey: ['record-events', recordId] })
    },
    onError: (e) => toast(e.message, 'error'),
  })
  const retire = useMutation({
    mutationFn: () => api.post(`/api/records/${recordId}/status`, { status: 'retired', reason: 'Retired from record drawer' }),
    onSuccess: () => {
      toast('Record retired')
      queryClient.invalidateQueries()
    },
    onError: (e) => toast(e.message, 'error'),
  })

  async function printLabel() {
    const resp = await fetch('/api/labels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record_ids: [recordId], format: 'avery5160' }),
    })
    if (!resp.ok) { toast('Label failed', 'error'); return }
    const blob = await resp.blob()
    window.open(URL.createObjectURL(blob), '_blank')
  }

  if (!recordId) return null
  const r = record.data

  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-slate-900/40"
         onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="h-full w-full max-w-xl overflow-y-auto bg-white p-6 shadow-2xl">
        {!r || !form ? (
          <div className="py-20 text-center text-slate-500">Loading…</div>
        ) : (
          <>
            <div className="mb-4 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <StatusBadge status={r.status} />
                  {r.duplicate_group_id && <Badge tone="amber">duplicate</Badge>}
                </div>
                <h2 className="mt-1.5 truncate text-lg font-semibold">{r.title || r.name}</h2>
                <p className="truncate text-xs text-slate-500">{r.source_name} · {r.path}</p>
              </div>
              <div className="flex shrink-0 gap-2">
                {r.status !== 'missing' && (
                  <button className="btn-secondary" onClick={() => setViewing(true)} title="View the PDF">
                    <Eye size={15} /> View
                  </button>
                )}
                <button className="btn-secondary" onClick={printLabel} title="Print label">
                  <Printer size={15} /> Label
                </button>
                <button className="rounded p-1.5 text-slate-400 hover:bg-slate-100" onClick={onClose}>
                  <X size={18} />
                </button>
              </div>
            </div>

            <div className="mb-4 grid grid-cols-3 gap-2 rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
              <div><span className="block text-slate-400">Size</span>{fmtBytes(r.size_bytes)}</div>
              <div><span className="block text-slate-400">File date</span>{fmtDate(r.file_mtime)}</div>
              <div><span className="block text-slate-400">Last seen</span>{fmtDate(r.last_seen_at)}</div>
              <div className="col-span-3 break-all">
                <span className="block text-slate-400">{r.hash_algo || 'hash'}</span>{r.hash_value || '—'}
              </div>
              {r.retention_until && (
                <div><span className="block text-slate-400">Retain until</span>{r.retention_until}</div>
              )}
            </div>

            {r.status !== 'retired' && r.status !== 'superseded' && (
              <div className="space-y-3">
                <Field label="Title">
                  <input className="input" value={form.title}
                         onChange={(e) => setForm({ ...form, title: e.target.value })} />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Project">
                    <select className="input" value={form.project_id ?? ''}
                            onChange={(e) => setForm({ ...form, project_id: e.target.value ? +e.target.value : null })}>
                      <option value="">—</option>
                      {projects.data?.filter((p) => p.is_active || p.id === form.project_id).map((p) => (
                        <option key={p.id} value={p.id}>{p.code} — {p.name}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Record type">
                    <select className="input" value={form.record_type_id ?? ''}
                            onChange={(e) => setForm({ ...form, record_type_id: e.target.value ? +e.target.value : null })}>
                      <option value="">—</option>
                      {types.data?.filter((t) => t.is_active || t.id === form.record_type_id).map((t) => (
                        <option key={t.id} value={t.id}>{t.code} — {t.name}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Lot number">
                    <input className="input" value={form.lot_number}
                           onChange={(e) => setForm({ ...form, lot_number: e.target.value })} />
                  </Field>
                  <Field label="Part number">
                    <input className="input" value={form.part_number}
                           onChange={(e) => setForm({ ...form, part_number: e.target.value })} />
                  </Field>
                  <Field label="Box (physical)">
                    <select className="input" value={form.box_id ?? ''}
                            onChange={(e) => setForm({ ...form, box_id: e.target.value ? +e.target.value : null })}>
                      <option value="">—</option>
                      {boxes.data?.filter((b) => b.status === 'open' || b.id === form.box_id).map((b) => (
                        <option key={b.id} value={b.id}>{b.box_number} ({b.physical_location || 'no location'})</option>
                      ))}
                    </select>
                  </Field>
                </div>
                <Field label="Tags">
                  <TagInput value={form.tags} onChange={(tags) => setForm({ ...form, tags })} />
                </Field>
                <Field label="Notes">
                  <textarea className="input" rows={2} value={form.notes}
                            onChange={(e) => setForm({ ...form, notes: e.target.value })} />
                </Field>
                <div className="flex gap-2">
                  <button className="btn-primary" onClick={() => save.mutate(form)} disabled={save.isPending}>
                    Save changes
                  </button>
                  {isAdmin && r.status !== 'missing' && (
                    <button className="btn-danger ml-auto" disabled={retire.isPending}
                            onClick={() => window.confirm('Retire this record? It becomes read-only (never deleted).') && retire.mutate()}>
                      Retire
                    </button>
                  )}
                </div>
              </div>
            )}

            {dupes.data?.length > 0 && (
              <div className="mt-6">
                <h3 className="mb-2 text-sm font-semibold text-amber-700">Possible duplicates</h3>
                <ul className="space-y-1 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs">
                  {dupes.data.map((d) => (
                    <li key={d.id} className="truncate">{d.source_name}: {d.path}</li>
                  ))}
                </ul>
              </div>
            )}

            <OcrPanel recordId={recordId} />

            <div className="mt-6">
              <h3 className="mb-2 text-sm font-semibold">History</h3>
              <ul className="space-y-2">
                {events.data?.map((ev) => (
                  <li key={ev.id} className="flex gap-3 text-xs">
                    <span className="w-32 shrink-0 text-slate-400">{fmtDate(ev.occurred_at)}</span>
                    <div>
                      <span className="font-medium capitalize">{ev.event_type.replace('_', ' ')}</span>
                      {ev.old_value?.path && ev.new_value?.path && ev.old_value.path !== ev.new_value.path && (
                        <span className="block text-slate-500">{ev.old_value.path} → {ev.new_value.path}</span>
                      )}
                    </div>
                  </li>
                ))}
                {events.data?.length === 0 && <li className="text-xs text-slate-400">No events recorded.</li>}
              </ul>
            </div>
          </>
        )}
      </div>
      {viewing && r && <PdfViewer record={r} onClose={() => setViewing(false)} />}
    </div>
  )
}
