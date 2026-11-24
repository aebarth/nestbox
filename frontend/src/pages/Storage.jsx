import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderTree } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Badge, Field, Modal, Spinner, fmtBytes, useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

/** Admin → Storage (#15 PR D): install mounts (read-only) + per-org location
 *  editor. The /api/storage endpoints are authz-filtered server-side: install
 *  admins see/manage every active org; org owners/admins only their own.
 */
const LOCATION_LABELS = {
  inbox: 'Inbox (scanner drop folder)',
  sources: 'Sources root (permanent documents)',
  backups: 'Backup path (org backup zips)',
}

function HealthBadge({ h }) {
  if (!h) return <Badge>unknown</Badge>
  if (h.writable) return <Badge tone="green">ok</Badge>
  if (h.exists) return <Badge tone="amber">read-only</Badge>
  return <Badge tone="red">missing</Badge>
}

export default function Storage({ user }) {
  const toast = useToast()
  const storage = useQuery({ queryKey: ['storage'], queryFn: () => api.get('/api/storage') })
  const orgs = storage.data?.orgs || []

  const [orgId, setOrgId] = useState(null)
  const selected = orgs.find((o) => o.id === orgId) || orgs[0]

  const [editing, setEditing] = useState(false)
  const [notices, setNotices] = useState([])

  if (storage.isLoading) return <Spinner label="Loading storage…" />
  if (!selected) return <p className="text-sm text-slate-500">No organization available.</p>

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <FolderTree size={22} className="text-amber-500" /> Storage
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-slate-500">
          Where each organization's files live. The install mounts are the physical substrate
          (fixed at container start); each org keeps its inbox, sources and backups in its own
          subtree underneath.
        </p>
      </div>

      {notices.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800" role="alert">
          {notices.map((n, i) => <p key={i}>{n}</p>)}
          <button className="mt-1 text-xs underline" onClick={() => setNotices([])}>Dismiss</button>
        </div>
      )}

      <section className="card">
        <div className="border-b border-slate-100 px-5 py-3">
          <h2 className="font-semibold">Install mounts</h2>
        </div>
        <table className="w-full">
          <thead><tr className="border-b border-slate-100">
            <th className="th">Location</th><th className="th">Container path</th>
            <th className="th">Host path (.env)</th><th className="th">Health</th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {(storage.data?.install || []).map((m) => (
              <tr key={m.location}>
                <td className="td font-medium">{LOCATION_LABELS[m.location] || m.location}</td>
                <td className="td font-mono text-xs text-slate-700">{m.container_path}</td>
                <td className="td font-mono text-xs text-slate-500">{m.host_path || '—'}</td>
                <td className="td"><HealthBadge h={m.health} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
          Install roots are fixed at container start. To change them, edit the NESTBOX_*_HOST_PATH
          variables in <code>.env</code> and recreate the containers — see docs/storage-options.md
          in the repository.
        </p>
      </section>

      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
          <h2 className="font-semibold">Organization locations — {selected.name}</h2>
          <div className="flex items-center gap-2">
            {orgs.length > 1 && (
              <select aria-label="Organization" className="input w-56" value={selected.id}
                      onChange={(e) => setOrgId(Number(e.target.value))}>
                {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            )}
            <button className="btn-primary" onClick={() => setEditing(true)}>Change locations</button>
          </div>
        </div>
        <table className="w-full">
          <thead><tr className="border-b border-slate-100">
            <th className="th">Location</th><th className="th">Path</th>
            <th className="th">Health</th><th className="th">Free</th><th className="th" />
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {['inbox', 'sources', 'backups'].map((loc) => (
              <tr key={loc}>
                <td className="td font-medium">{LOCATION_LABELS[loc]}</td>
                <td className="td font-mono text-xs text-slate-700">{selected.storage?.[loc] || '—'}</td>
                <td className="td"><HealthBadge h={selected.health?.[loc]} /></td>
                <td className="td text-xs text-slate-500">{fmtBytes(selected.health?.[loc]?.free_bytes) || '—'}</td>
                <td className="td text-right">
                  <TestButton orgId={selected.id} location={loc} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
          Paths must stay under the matching install mount. File-based sources are confined to the
          sources root; Drive API sources (Google / OneDrive) are exempt. Run, list and restore org
          backups on the <Link to="/backups" className="text-amber-600 underline">Backups</Link> page.
        </p>
      </section>

      {editing && (
        <EditLocationsModal org={selected} onClose={() => setEditing(false)} onWarnings={setNotices} />
      )}
    </div>
  )
}

function TestButton({ orgId, location }) {
  const toast = useToast()
  const probe = useMutation({
    mutationFn: () => api.post('/api/storage/test', { location, org_id: orgId }),
    onSuccess: (res) =>
      toast(res.ok ? `✓ ${location} is writable` : res.detail || `${location} is not writable`, res.ok ? 'ok' : 'error'),
    onError: (e) => toast(e.message, 'error'),
  })
  return (
    <button className="btn-secondary" disabled={probe.isPending} onClick={() => probe.mutate()}>
      {probe.isPending ? 'Testing…' : 'Test'}
    </button>
  )
}

function EditLocationsModal({ org, onClose, onWarnings }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [values, setValues] = useState({
    inbox_root: org.storage?.inbox || '',
    sources_root: org.storage?.sources || '',
    backup_path: org.storage?.backups || '',
  })
  const [confirmSources, setConfirmSources] = useState(false)

  const changed = {
    inbox_root: values.inbox_root !== (org.storage?.inbox || ''),
    sources_root: values.sources_root !== (org.storage?.sources || ''),
    backup_path: values.backup_path !== (org.storage?.backups || ''),
  }
  const anyChanged = Object.values(changed).some(Boolean)
  // Mirrors the server guardrail: changing the sources root while records
  // exist requires explicit confirmation — files are NOT migrated.
  const needsConfirm = changed.sources_root && org.record_count > 0

  const save = useMutation({
    mutationFn: () => {
      // Only changed fields are sent; PUT leaves omitted locations untouched.
      const body = {}
      if (changed.inbox_root) body.inbox_root = values.inbox_root
      if (changed.sources_root) body.sources_root = values.sources_root
      if (changed.backup_path) body.backup_path = values.backup_path
      if (needsConfirm) body.confirm_sources_change = confirmSources
      return api.put(`/api/storage/org/${org.id}`, body)
    },
    onSuccess: (res) => {
      toast('Storage locations updated')
      onWarnings(res.warnings || [])
      queryClient.invalidateQueries({ queryKey: ['storage'] })
      onClose()
    },
    onError: (e) => toast(e.message, 'error'),
  })

  const set = (k) => (e) => setValues((v) => ({ ...v, [k]: e.target.value }))

  return (
    <Modal title={`Change locations — ${org.name}`} onClose={onClose} wide>
      <div className="space-y-3">
        <Field label="Inbox (scanner drop folder)" hint={`Must live under the /inbox mount, e.g. /inbox/${org.slug}. An unfiled PDFs already in the old inbox stop being discovered.`}>
          <input className="input" value={values.inbox_root} onChange={set('inbox_root')} />
        </Field>
        <Field label="Sources root (permanent documents)"
               hint={`Must live under the /sources mount, e.g. /sources/${org.slug}. File-based sources are confined here; Drive API sources are exempt.`}>
          <input className="input" value={values.sources_root} onChange={set('sources_root')} />
        </Field>
        <Field label="Backup path (org backup zips)"
               hint={`Must live under the /backups mount, e.g. /backups/${org.slug}. Changing it never deletes existing dumps.`}>
          <input className="input" value={values.backup_path} onChange={set('backup_path')} />
        </Field>
        {needsConfirm && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <p>
              This organization already has {org.record_count} records. Changing the sources root will
              NOT move existing files — record paths would go stale (run a scan to re-discover them).
            </p>
            <label className="mt-2 flex items-start gap-2">
              <input type="checkbox" className="mt-0.5" checked={confirmSources}
                     onChange={(e) => setConfirmSources(e.target.checked)} />
              I understand existing files will not be migrated
            </label>
          </div>
        )}
        <button className="btn-primary w-full"
                disabled={!anyChanged || save.isPending || (needsConfirm && !confirmSources)}
                onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save locations'}
        </button>
      </div>
    </Modal>
  )
}
