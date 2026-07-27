import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellOff } from 'lucide-react'
import { useState } from 'react'
import { EmptyState, Field, Modal, SeverityBadge, Spinner, StatusBadge, fmtDate, useToast } from '../components/ui.jsx'
import RecordDrawer from '../components/RecordDrawer.jsx'
import { api } from '../services/api.js'

export default function Alerts() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('unresolved')
  const [resolving, setResolving] = useState(null)
  const [resolution, setResolution] = useState('accepted')
  const [notes, setNotes] = useState('')
  const [openRecord, setOpenRecord] = useState(null)

  const alerts = useQuery({
    queryKey: ['alerts', filter],
    queryFn: () => api.get(`/api/alerts?status=${filter}&limit=200`),
  })

  const review = useMutation({
    mutationFn: ({ id, body }) => api.post(`/api/alerts/${id}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
      queryClient.invalidateQueries({ queryKey: ['records'] })
      setResolving(null)
      setNotes('')
    },
    onError: (e) => toast(e.message, 'error'),
  })

  const HELP = {
    missing: 'The file was not found at its last known location.',
    modified: 'The file content changed since the last scan.',
    hash_mismatch: 'Content changed although the file date did not — possible silent corruption.',
    duplicate: 'Two or more files appear to be the same document.',
    moved: 'The file moved to a different folder.',
    renamed: 'The file name changed.',
    discovered: 'A new file appeared in a monitored location.',
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Alerts</h1>
        <select className="input w-44" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="unresolved">Open + acknowledged</option>
          <option value="open">Open only</option>
          <option value="resolved">Resolved</option>
          <option value="all">All</option>
        </select>
      </div>

      {alerts.isLoading ? <Spinner /> : !alerts.data?.length ? (
        <div className="card">
          <EmptyState icon={BellOff} title="No alerts here">
            Monitoring raises an alert when a tracked file goes missing, changes, moves, or has duplicates.
          </EmptyState>
        </div>
      ) : (
        <div className="card divide-y divide-slate-100">
          {alerts.data.map((a) => (
            <div key={a.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
              <SeverityBadge severity={a.severity} />
              <StatusBadge status={a.status} />
              <div className="min-w-0 flex-1">
                <button className="block max-w-full truncate text-left text-sm font-medium hover:text-amber-600"
                        onClick={() => setOpenRecord(a.record_id)}>
                  {a.record_name || `Record #${a.record_id}`}
                </button>
                <div className="truncate text-xs text-slate-500">
                  <span className="font-medium capitalize">{a.alert_type.replace('_', ' ')}</span>
                  {' — '}{HELP[a.alert_type] || ''} {a.source_name && `(${a.source_name})`} · {fmtDate(a.created_at)}
                </div>
                {a.notes && <div className="text-xs italic text-slate-400">{a.notes}</div>}
              </div>
              {a.status !== 'resolved' && (
                <div className="flex gap-2">
                  {a.status === 'open' && (
                    <button className="btn-secondary"
                            onClick={() => review.mutate({ id: a.id, body: { status: 'acknowledged' } })}>
                      Acknowledge
                    </button>
                  )}
                  <button className="btn-primary" onClick={() => { setResolving(a); setResolution('accepted') }}>
                    Resolve
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {resolving && (
        <Modal title={`Resolve: ${resolving.alert_type.replace('_', ' ')}`} onClose={() => setResolving(null)}>
          <div className="space-y-3">
            <Field label="Resolution">
              <select className="input" value={resolution} onChange={(e) => setResolution(e.target.value)}>
                <option value="accepted">Accepted — the change was expected/authorized</option>
                <option value="restored">Restored — the file was put back</option>
                {resolving.alert_type === 'missing' && (
                  <option value="retired">Retired — the record is gone intentionally; retire it</option>
                )}
                <option value="false_positive">False positive</option>
              </select>
            </Field>
            <Field label="Notes (recorded in the audit trail)">
              <textarea className="input" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
            <button className="btn-primary w-full" disabled={review.isPending}
                    onClick={() => review.mutate({ id: resolving.id, body: { status: 'resolved', resolution, notes } })}>
              Resolve alert
            </button>
          </div>
        </Modal>
      )}

      {openRecord && <RecordDrawer recordId={openRecord} onClose={() => setOpenRecord(null)} />}
    </div>
  )
}
