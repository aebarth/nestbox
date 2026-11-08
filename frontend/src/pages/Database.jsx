import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database as DbIcon, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { api } from '../services/api.js'

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleString() : '—'
}

/** Type-to-confirm control: the action button stays disabled until the
 *  caller types the expected word exactly (RESTORE / RESET). */
function ConfirmAction({ label, icon: Icon, expected, onConfirm, busy }) {
  const [value, setValue] = useState('')
  const [confirming, setConfirming] = useState(false)

  if (!confirming) {
    return (
      <button onClick={() => setConfirming(true)} className="text-xs text-slate-500 hover:text-slate-800">
        {label}
      </button>
    )
  }

  return (
    <span className="flex items-center gap-1.5">
      <input
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={`type ${expected}`}
        aria-label={`Confirm by typing ${expected}`}
        className="w-24 rounded border border-slate-300 px-2 py-1 text-xs"
      />
      <button
        disabled={value !== expected || busy}
        onClick={() => { onConfirm(); setValue(''); setConfirming(false) }}
        className="flex items-center gap-1 rounded bg-rose-600 px-2 py-1 text-xs font-medium text-white disabled:opacity-40"
      >
        <Icon size={12} /> Confirm
      </button>
      <button onClick={() => { setValue(''); setConfirming(false) }} className="text-xs text-slate-400 hover:text-slate-700">
        Cancel
      </button>
    </span>
  )
}

export default function Database() {
  const queryClient = useQueryClient()
  const [error, setError] = useState('')

  const backups = useQuery({
    queryKey: ['backups'],
    queryFn: () => api.get('/api/admin/backups'),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['backups'] })

  const createBackup = useMutation({
    mutationFn: () => api.post('/api/admin/backups', {}),
    onSuccess: invalidate,
    onError: (e) => setError(e.message),
  })

  const restore = useMutation({
    mutationFn: (id) => api.post(`/api/admin/backups/${id}/restore`, { confirm: 'RESTORE' }),
    onSuccess: () => { setError(''); invalidate() },
    onError: (e) => setError(e.message),
  })

  const reset = useMutation({
    mutationFn: () => api.post('/api/admin/reset', { confirm: 'RESET' }),
    onSuccess: () => { setError(''); invalidate() },
    onError: (e) => setError(e.message),
  })

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold text-slate-900">
          <DbIcon size={20} className="text-amber-500" /> Database
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Backups are checksummed pg_dump files. Restoring replaces the live database;
          reset wipes everything (a pre-reset backup is taken automatically).
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700" role="alert">
          {error}
        </div>
      )}

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-800">Backups</h2>
          <button
            onClick={() => createBackup.mutate()}
            disabled={createBackup.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-amber-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
          >
            <Plus size={14} /> {createBackup.isPending ? 'Backing up…' : 'Create backup'}
          </button>
        </div>
        {backups.isLoading ? (
          <div className="px-5 py-6 text-sm text-slate-400">Loading backups…</div>
        ) : (backups.data || []).length === 0 ? (
          <div className="px-5 py-6 text-sm text-slate-400">No backups yet.</div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-5 py-2">File</th>
                <th className="px-3 py-2">Kind</th>
                <th className="px-3 py-2">Size</th>
                <th className="px-3 py-2">Created</th>
                <th className="px-5 py-2 text-right">Restore</th>
              </tr>
            </thead>
            <tbody>
              {(backups.data || []).map((b) => (
                <tr key={b.id} className="border-t border-slate-100">
                  <td className="px-5 py-2.5 font-mono text-xs text-slate-700" title={b.sha256}>
                    {b.filename}
                    <div className="text-[10px] text-slate-400">sha256 {b.sha256.slice(0, 16)}…</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                      b.kind === 'scheduled' ? 'bg-slate-100 text-slate-600' : 'bg-amber-50 text-amber-700'
                    }`}>{b.kind}</span>
                  </td>
                  <td className="px-3 py-2.5 text-slate-600">{fmtSize(b.size_bytes)}</td>
                  <td className="px-3 py-2.5 text-slate-600">{fmtDate(b.created_at)}</td>
                  <td className="px-5 py-2.5 text-right">
                    <ConfirmAction
                      label="Restore"
                      icon={RotateCcw}
                      expected="RESTORE"
                      busy={restore.isPending}
                      onConfirm={() => restore.mutate(b.id)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="rounded-xl border border-rose-200 bg-rose-50/40 p-5">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-rose-800">
          <Trash2 size={15} /> Danger zone
        </h2>
        <p className="mt-1 text-xs text-rose-700/80">
          Reset drops all data — records, boxes, users, audit trail — and rebuilds the schema.
          A pre-reset backup is created automatically and kept in the backup folder.
        </p>
        <div className="mt-3">
          <ConfirmAction
            label="Reset database to a clean slate"
            icon={Trash2}
            expected="RESET"
            busy={reset.isPending}
            onConfirm={() => reset.mutate()}
          />
        </div>
      </section>
    </div>
  )
}
