import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { HardDrive, Plus, RotateCcw } from 'lucide-react'
import { Field, Modal, Spinner, fmtBytes, fmtDate, useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

/** Org-scoped backup/restore controls (#15 PR C).
 *
 * The /api/storage endpoints are authz-filtered server-side: install admins
 * receive every active org (and may restore any org's backup into any org);
 * org owners/admins receive only their own org and can only restore into it.
 */
export default function OrgBackups({ user }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const isAdmin = user.role === 'admin'

  const storage = useQuery({ queryKey: ['storage'], queryFn: () => api.get('/api/storage') })
  const orgs = storage.data?.orgs || []

  const [orgId, setOrgId] = useState(null)
  const selected = orgs.find((o) => o.id === orgId) || orgs[0]

  const backups = useQuery({
    queryKey: ['org-backups', selected?.id],
    queryFn: () => api.get(`/api/storage/org/${selected.id}/backups`),
    enabled: !!selected,
  })

  const [notes, setNotes] = useState('')
  const runBackup = useMutation({
    mutationFn: () => api.post(`/api/storage/org/${selected.id}/backups`, { notes }),
    onSuccess: (b) => {
      toast(`Backup ${b.filename} created`)
      setNotes('')
      queryClient.invalidateQueries({ queryKey: ['org-backups', selected.id] })
    },
    onError: (e) => toast(e.message, 'error'),
  })

  const [restoring, setRestoring] = useState(null)

  if (storage.isLoading) return <Spinner label="Loading storage…" />
  if (!selected) return <p className="text-sm text-slate-500">No organization available.</p>

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <HardDrive size={22} className="text-amber-500" /> Organization backups
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Org-pure zips (data + file tree) stored in the org's backup path
            {selected.storage?.backups ? ` (${selected.storage.backups})` : ''}.
            Restoring fills an empty organization with fresh IDs.
          </p>
        </div>
        {orgs.length > 1 && (
          <select aria-label="Organization" className="input w-56" value={selected.id}
                  onChange={(e) => setOrgId(Number(e.target.value))}>
            {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        )}
      </div>

      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
          <h2 className="font-semibold">Backups — {selected.name}</h2>
          <div className="flex items-center gap-2">
            <input className="input w-48" placeholder="Notes (optional)" value={notes}
                   aria-label="Backup notes"
                   onChange={(e) => setNotes(e.target.value)} />
            <button className="btn-primary" disabled={runBackup.isPending} onClick={() => runBackup.mutate()}>
              <Plus size={14} /> {runBackup.isPending ? 'Backing up…' : 'Run backup'}
            </button>
          </div>
        </div>
        {backups.isLoading ? (
          <div className="px-5 py-6 text-sm text-slate-400">Loading backups…</div>
        ) : (backups.data || []).length === 0 ? (
          <div className="px-5 py-6 text-sm text-slate-400">No organization backups yet.</div>
        ) : (
          <table className="w-full">
            <thead><tr className="border-b border-slate-100">
              <th className="th">File</th><th className="th">Size</th><th className="th">Notes</th>
              <th className="th">By</th><th className="th">Created</th><th className="th" />
            </tr></thead>
            <tbody className="divide-y divide-slate-100">
              {(backups.data || []).map((b) => (
                <tr key={b.id}>
                  <td className="td font-mono text-xs text-slate-700">
                    {b.filename}
                    <div className="text-[10px] text-slate-400" title={b.sha256}>
                      sha256 {String(b.sha256).slice(0, 16)}…
                    </div>
                  </td>
                  <td className="td">{fmtBytes(b.size_bytes)}</td>
                  <td className="td text-xs text-slate-500">{b.notes || '—'}</td>
                  <td className="td text-xs">{b.created_by || '—'}</td>
                  <td className="td text-xs">{fmtDate(b.created_at)}</td>
                  <td className="td text-right">
                    <button className="btn-secondary" onClick={() => setRestoring(b)}>
                      <RotateCcw size={14} /> Restore
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {restoring && (
        <RestoreModal backup={restoring} orgs={orgs} isAdmin={isAdmin} onClose={() => setRestoring(null)} />
      )}
    </div>
  )
}

function RestoreModal({ backup, orgs, isAdmin, onClose }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [targetId, setTargetId] = useState(backup.org_id)
  const [confirmed, setConfirmed] = useState(false)
  const targetName = (orgs.find((o) => o.id === targetId) || {}).name || `organization ${targetId}`

  const restore = useMutation({
    mutationFn: () => api.post(
      `/api/storage/org/${backup.org_id}/backups/${backup.id}/restore`,
      // Org admins can only ever target the backup's own org — the server
      // enforces that; omitting target_org_id lets it default there.
      isAdmin ? { target_org_id: targetId, confirm: true } : { confirm: true },
    ),
    onSuccess: () => {
      toast(`Restored into ${targetName}`)
      queryClient.invalidateQueries({ queryKey: ['org-backups'] })
      onClose()
    },
    onError: (e) => toast(e.message, 'error'),
  })

  return (
    <Modal title={`Restore ${backup.filename}`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm text-slate-600">
          Restores this backup's records, boxes, projects and file tree into the target
          organization with fresh IDs. The target org must not already hold document data.
        </p>
        {isAdmin ? (
          <Field label="Target organization">
            <select className="input" value={targetId}
                    onChange={(e) => setTargetId(Number(e.target.value))}>
              {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          </Field>
        ) : (
          <p className="text-sm text-slate-500">Target: the backup's own organization ({targetName}).</p>
        )}
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5" checked={confirmed}
                 onChange={(e) => setConfirmed(e.target.checked)} />
          I understand this writes the backup's data into {targetName}.
        </label>
        <button className="btn-primary w-full" disabled={!confirmed || restore.isPending}
                onClick={() => restore.mutate()}>
          <RotateCcw size={14} /> {restore.isPending ? 'Restoring…' : 'Restore backup'}
        </button>
      </div>
    </Modal>
  )
}
