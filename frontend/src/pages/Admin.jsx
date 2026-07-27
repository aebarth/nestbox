import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, FolderOpen, Plus } from 'lucide-react'
import { useState } from 'react'
import { Badge, Field, Modal, Spinner, StatusBadge, fmtDate, useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

const TABS = ['Users', 'Sources', 'Projects', 'Record types', 'Settings', 'Scan history', 'Audit trail']

export default function Admin() {
  const [tab, setTab] = useState('Users')
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">Admin</h1>
      <div className="flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
                  className={`rounded-t-lg px-4 py-2 text-sm font-medium ${
                    tab === t ? 'border border-b-0 border-slate-200 bg-white text-amber-600'
                              : 'text-slate-500 hover:text-slate-800'
                  }`}>
            {t}
          </button>
        ))}
      </div>
      {tab === 'Users' && <Users />}
      {tab === 'Sources' && <Sources />}
      {tab === 'Projects' && <Lookup kind="projects" label="project" hasRetention={false} />}
      {tab === 'Record types' && <Lookup kind="record-types" label="record type" hasRetention />}
      {tab === 'Settings' && <Settings />}
      {tab === 'Scan history' && <ScanHistory />}
      {tab === 'Audit trail' && <Audit />}
    </div>
  )
}

function Users() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const users = useQuery({ queryKey: ['users'], queryFn: () => api.get('/api/admin/users') })
  const [editing, setEditing] = useState(null)

  const save = useMutation({
    mutationFn: (u) => u.id
      ? api.patch(`/api/admin/users/${u.id}`, { full_name: u.full_name, role: u.role, is_active: u.is_active, password: u.password || undefined })
      : api.post('/api/admin/users', u),
    onSuccess: () => { toast('User saved'); setEditing(null); queryClient.invalidateQueries({ queryKey: ['users'] }) },
    onError: (e) => toast(e.message, 'error'),
  })

  if (users.isLoading) return <Spinner />
  return (
    <div className="card">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold">Users</h2>
        <button className="btn-primary" onClick={() => setEditing({ username: '', password: '', full_name: '', role: 'user' })}>
          <Plus size={14} /> Add user
        </button>
      </div>
      <table className="w-full">
        <thead><tr className="border-b border-slate-100">
          <th className="th">Username</th><th className="th">Name</th><th className="th">Role</th>
          <th className="th">Status</th><th className="th">Last login</th><th className="th" />
        </tr></thead>
        <tbody className="divide-y divide-slate-100">
          {users.data.map((u) => (
            <tr key={u.id}>
              <td className="td font-medium">{u.username}</td>
              <td className="td">{u.full_name || '—'}</td>
              <td className="td"><Badge tone={u.role === 'admin' ? 'violet' : 'slate'}>{u.role}</Badge></td>
              <td className="td"><Badge tone={u.is_active ? 'green' : 'red'}>{u.is_active ? 'active' : 'disabled'}</Badge></td>
              <td className="td text-xs">{fmtDate(u.last_login_at)}</td>
              <td className="td text-right">
                <button className="btn-secondary" onClick={() => setEditing({ ...u, password: '' })}>Edit</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && (
        <Modal title={editing.id ? `Edit ${editing.username}` : 'Add user'} onClose={() => setEditing(null)}>
          <div className="space-y-3">
            {!editing.id && (
              <Field label="Username">
                <input className="input" value={editing.username}
                       onChange={(e) => setEditing({ ...editing, username: e.target.value })} />
              </Field>
            )}
            <Field label="Full name">
              <input className="input" value={editing.full_name}
                     onChange={(e) => setEditing({ ...editing, full_name: e.target.value })} />
            </Field>
            <Field label="Role" hint="Admins manage users, sources, lookups and settings. Users search, categorize and file records.">
              <select className="input" value={editing.role}
                      onChange={(e) => setEditing({ ...editing, role: e.target.value })}>
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </Field>
            <Field label={editing.id ? 'New password (leave blank to keep)' : 'Password'}>
              <input className="input" type="password" value={editing.password}
                     onChange={(e) => setEditing({ ...editing, password: e.target.value })} />
            </Field>
            {editing.id && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={editing.is_active}
                       onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
                Account active
              </label>
            )}
            <button className="btn-primary w-full" disabled={save.isPending}
                    onClick={() => save.mutate(editing)}>Save user</button>
          </div>
        </Modal>
      )}
    </div>
  )
}

const SOURCE_EMPTY = { name: '', kind: 'local', config: {}, is_monitored: true, scan_cron: '', is_active: true }

function Sources() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const sources = useQuery({ queryKey: ['sources'], queryFn: () => api.get('/api/sources') })
  const [editing, setEditing] = useState(null)
  const [driveSearch, setDriveSearch] = useState('')
  const [driveResults, setDriveResults] = useState(null)

  const save = useMutation({
    mutationFn: (s) => s.id ? api.patch(`/api/sources/${s.id}`, s) : api.post('/api/sources', s),
    onSuccess: () => { toast('Source saved'); setEditing(null); queryClient.invalidateQueries({ queryKey: ['sources'] }) },
    onError: (e) => toast(e.message, 'error'),
  })

  async function testSource(s) {
    const res = await api.post(`/api/sources/${s.id}/test`)
    toast(res.ok ? `✓ ${s.name} looks good` : res.detail, res.ok ? 'ok' : 'error')
  }

  async function connectGoogle(s) {
    try {
      const { url } = await api.get(`/api/connectors/google/authorize?source_id=${s.id}`)
      window.location.href = url
    } catch (e) { toast(e.message, 'error') }
  }

  async function searchDrives() {
    try {
      setDriveResults(await api.get(`/api/connectors/graph/drives?q=${encodeURIComponent(driveSearch)}`))
    } catch (e) { toast(e.message, 'error') }
  }

  if (sources.isLoading) return <Spinner />
  const kindLabel = { local: 'Local / network share', gdrive: 'Google Drive', graph_drive: 'OneDrive / SharePoint' }
  return (
    <div className="card">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold">Storage locations</h2>
        <button className="btn-primary" onClick={() => setEditing({ ...SOURCE_EMPTY })}>
          <Plus size={14} /> Add source
        </button>
      </div>
      <table className="w-full">
        <thead><tr className="border-b border-slate-100">
          <th className="th">Name</th><th className="th">Kind</th><th className="th">Location</th>
          <th className="th">Monitored</th><th className="th">Last scan</th><th className="th" />
        </tr></thead>
        <tbody className="divide-y divide-slate-100">
          {sources.data.map((s) => (
            <tr key={s.id} className={s.is_active ? '' : 'opacity-50'}>
              <td className="td font-medium">
                {s.name} {s.config.builtin_inbox && <Badge tone="blue">inbox</Badge>}
              </td>
              <td className="td text-sm">{kindLabel[s.kind]}</td>
              <td className="td max-w-xs truncate text-xs text-slate-500">
                {s.kind === 'local' ? s.config.root
                  : s.kind === 'gdrive' ? (s.has_credentials ? `folder ${s.config.folder_id || 'root'}` : 'not connected')
                  : s.config.drive_id ? `${s.config.drive_id.slice(0, 18)}…${s.config.folder_path ? '/' + s.config.folder_path : ''}` : 'no drive set'}
              </td>
              <td className="td"><Badge tone={s.is_monitored ? 'green' : 'slate'}>{s.is_monitored ? 'yes' : 'no'}</Badge></td>
              <td className="td text-xs">{fmtDate(s.last_scan_at)}</td>
              <td className="td text-right">
                <div className="flex justify-end gap-2">
                  {s.kind === 'gdrive' && (
                    <button className="btn-secondary" onClick={() => connectGoogle(s)}>
                      <Cloud size={14} /> {s.has_credentials ? 'Reconnect' : 'Connect Google'}
                    </button>
                  )}
                  <button className="btn-secondary" onClick={() => testSource(s)}>Test</button>
                  <button className="btn-secondary" onClick={() => setEditing({ ...s, scan_cron: s.scan_cron || '' })}>Edit</button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {editing && (
        <Modal title={editing.id ? `Edit ${editing.name}` : 'Add source'} onClose={() => setEditing(null)} wide>
          <div className="space-y-3">
            <Field label="Name">
              <input className="input" value={editing.name}
                     onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
            </Field>
            {!editing.config.builtin_inbox && (
              <Field label="Kind">
                <select className="input" value={editing.kind} disabled={!!editing.id}
                        onChange={(e) => setEditing({ ...editing, kind: e.target.value, config: {} })}>
                  <option value="local">Local / network share (under /sources)</option>
                  <option value="gdrive">Google Drive</option>
                  <option value="graph_drive">OneDrive / SharePoint</option>
                </select>
              </Field>
            )}
            {editing.kind === 'local' && !editing.config.builtin_inbox && (
              <Field label="Folder" hint="Inside the container, e.g. /sources/quality-records. NESTBOX_SOURCES_PATH in .env maps your real folder to /sources.">
                <div className="flex items-center gap-2">
                  <FolderOpen size={16} className="text-slate-400" />
                  <input className="input" value={editing.config.root || ''}
                         placeholder="/sources/…"
                         onChange={(e) => setEditing({ ...editing, config: { ...editing.config, root: e.target.value } })} />
                </div>
              </Field>
            )}
            {editing.kind === 'gdrive' && (
              <Field label="Google Drive folder ID" hint="From the folder's URL: drive.google.com/drive/folders/<ID>. Leave blank for the whole Drive. Save, then press Connect Google.">
                <input className="input" value={editing.config.folder_id || ''}
                       onChange={(e) => setEditing({ ...editing, config: { ...editing.config, folder_id: e.target.value } })} />
              </Field>
            )}
            {editing.kind === 'graph_drive' && (
              <>
                <Field label="Find a drive" hint="Enter a user email for their OneDrive, or a keyword to search SharePoint sites. Requires Microsoft 365 settings to be configured.">
                  <div className="flex gap-2">
                    <input className="input" value={driveSearch} onChange={(e) => setDriveSearch(e.target.value)} />
                    <button className="btn-secondary" onClick={searchDrives}>Search</button>
                  </div>
                </Field>
                {driveResults && (
                  <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-slate-200 p-2">
                    {driveResults.length === 0 && <p className="p-2 text-sm text-slate-500">Nothing found.</p>}
                    {driveResults.map((d) => (
                      <button key={d.drive_id}
                              className={`block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-amber-50 ${
                                editing.config.drive_id === d.drive_id ? 'bg-amber-100' : ''
                              }`}
                              onClick={() => setEditing({ ...editing, config: { ...editing.config, drive_id: d.drive_id } })}>
                        {d.name}
                      </button>
                    ))}
                  </div>
                )}
                <Field label="Drive ID">
                  <input className="input" value={editing.config.drive_id || ''}
                         onChange={(e) => setEditing({ ...editing, config: { ...editing.config, drive_id: e.target.value } })} />
                </Field>
                <Field label="Folder path inside the drive (optional)" hint="e.g. Quality/Records">
                  <input className="input" value={editing.config.folder_path || ''}
                         onChange={(e) => setEditing({ ...editing, config: { ...editing.config, folder_path: e.target.value } })} />
                </Field>
              </>
            )}
            <div className="grid grid-cols-2 gap-3">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={editing.is_monitored}
                       onChange={(e) => setEditing({ ...editing, is_monitored: e.target.checked })} />
                Monitor for changes
              </label>
              {!editing.config.builtin_inbox && (
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={editing.is_active}
                         onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
                  Active
                </label>
              )}
            </div>
            <Field label="Custom scan schedule (cron, optional)" hint="Overrides the global schedule for this source, e.g. 0 */4 * * * for every 4 hours.">
              <input className="input" value={editing.scan_cron || ''}
                     onChange={(e) => setEditing({ ...editing, scan_cron: e.target.value || null })} />
            </Field>
            <button className="btn-primary w-full" disabled={!editing.name || save.isPending}
                    onClick={() => save.mutate(editing)}>Save source</button>
          </div>
        </Modal>
      )}
    </div>
  )
}

function Lookup({ kind, label, hasRetention }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const items = useQuery({ queryKey: [kind], queryFn: () => api.get(`/api/lookups/${kind}`) })
  const [editing, setEditing] = useState(null)

  const save = useMutation({
    mutationFn: (item) => item.id
      ? api.patch(`/api/lookups/${kind}/${item.id}`, item)
      : api.post(`/api/lookups/${kind}`, item),
    onSuccess: () => { toast('Saved'); setEditing(null); queryClient.invalidateQueries({ queryKey: [kind] }) },
    onError: (e) => toast(e.message, 'error'),
  })

  if (items.isLoading) return <Spinner />
  return (
    <div className="card">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold capitalize">{label}s</h2>
        <button className="btn-primary"
                onClick={() => setEditing({ code: '', name: '', retention_years: null, is_active: true })}>
          <Plus size={14} /> Add {label}
        </button>
      </div>
      <table className="w-full">
        <thead><tr className="border-b border-slate-100">
          <th className="th">Code</th><th className="th">Name</th>
          {hasRetention && <th className="th">Retention</th>}
          <th className="th">Status</th><th className="th" />
        </tr></thead>
        <tbody className="divide-y divide-slate-100">
          {items.data.map((it) => (
            <tr key={it.id} className={it.is_active ? '' : 'opacity-50'}>
              <td className="td font-mono text-sm font-medium">{it.code}</td>
              <td className="td">{it.name}</td>
              {hasRetention && (
                <td className="td text-sm">{it.retention_years ? `${it.retention_years} years` : 'permanent'}</td>
              )}
              <td className="td"><Badge tone={it.is_active ? 'green' : 'slate'}>{it.is_active ? 'active' : 'inactive'}</Badge></td>
              <td className="td text-right">
                <button className="btn-secondary" onClick={() => setEditing({ ...it })}>Edit</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && (
        <Modal title={editing.id ? `Edit ${editing.code}` : `Add ${label}`} onClose={() => setEditing(null)}>
          <div className="space-y-3">
            <Field label="Code" hint="Short code shown on labels and in tables.">
              <input className="input" value={editing.code}
                     onChange={(e) => setEditing({ ...editing, code: e.target.value })} />
            </Field>
            <Field label="Name">
              <input className="input" value={editing.name}
                     onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
            </Field>
            {hasRetention && (
              <Field label="Retention (years)" hint="Blank = keep permanently. Sets each record's retain-until date when filed.">
                <input className="input" type="number" min="0" value={editing.retention_years ?? ''}
                       onChange={(e) => setEditing({ ...editing, retention_years: e.target.value ? +e.target.value : null })} />
              </Field>
            )}
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={editing.is_active}
                     onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
              Active (available for new records)
            </label>
            <button className="btn-primary w-full" disabled={!editing.code || !editing.name || save.isPending}
                    onClick={() => save.mutate(editing)}>Save</button>
          </div>
        </Modal>
      )}
    </div>
  )
}

function Settings() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => api.get('/api/admin/settings') })
  const [form, setForm] = useState(null)

  const save = useMutation({
    mutationFn: (body) => api.put('/api/admin/settings', body),
    onSuccess: () => { toast('Settings saved'); queryClient.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e) => toast(e.message, 'error'),
  })

  if (settings.isLoading) return <Spinner />
  const s = settings.data
  const f = form || {
    org_name: s.org_name || '',
    schedule_cron: s.schedule_cron || '0 2 * * *',
    integrity_cron: s.integrity_cron || '0 3 * * 0',
    graph_tenant_id: s.graph_tenant_id || '',
    graph_client_id: s.graph_client_id || '',
    graph_client_secret: '',
    google_client_id: s.google_client_id || '',
    google_client_secret: '',
  }
  const set = (k) => (e) => setForm({ ...f, [k]: e.target.value })

  function submit() {
    const body = {
      org_name: f.org_name,
      schedule_cron: f.schedule_cron,
      integrity_cron: f.integrity_cron,
      graph_tenant_id: f.graph_tenant_id,
      graph_client_id: f.graph_client_id,
      google_client_id: f.google_client_id,
      graph_client_secret_enc: f.graph_client_secret,
      google_client_secret_enc: f.google_client_secret,
    }
    save.mutate(body)
  }

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <div className="card space-y-3 p-5">
        <h2 className="font-semibold">General</h2>
        <Field label="Organization name">
          <input className="input" value={f.org_name} onChange={set('org_name')} />
        </Field>
        <Field label="Scan schedule (cron)" hint="Default: 0 2 * * * = every night at 2:00. All monitored sources.">
          <input className="input" value={f.schedule_cron} onChange={set('schedule_cron')} />
        </Field>
        <Field label="Integrity check schedule (cron)" hint="Default: 0 3 * * 0 = Sundays 3:00. Verifies stored files haven't silently changed.">
          <input className="input" value={f.integrity_cron} onChange={set('integrity_cron')} />
        </Field>
      </div>

      <div className="card space-y-3 p-5">
        <h2 className="font-semibold">Microsoft 365 (OneDrive / SharePoint)</h2>
        <p className="text-xs text-slate-500">
          Register a single-tenant app in Entra ID with application permissions Files.ReadWrite.All
          and Sites.ReadWrite.All — see docs/microsoft-365-setup.md in the Nestbox repository.
        </p>
        <Field label="Tenant ID">
          <input className="input" value={f.graph_tenant_id} onChange={set('graph_tenant_id')} />
        </Field>
        <Field label="Client ID">
          <input className="input" value={f.graph_client_id} onChange={set('graph_client_id')} />
        </Field>
        <Field label={`Client secret ${s.graph_client_secret_enc?.configured ? '(configured — enter to replace)' : ''}`}>
          <input className="input" type="password" value={f.graph_client_secret} onChange={set('graph_client_secret')} />
        </Field>
      </div>

      <div className="card space-y-3 p-5">
        <h2 className="font-semibold">Google Drive</h2>
        <p className="text-xs text-slate-500">
          Create an OAuth web client in Google Cloud Console with redirect URI
          {' '}<code className="rounded bg-slate-100 px-1">{window.location.origin}/api/connectors/google/callback</code>
          {' '}— see docs/google-drive-setup.md.
        </p>
        <Field label="Client ID">
          <input className="input" value={f.google_client_id} onChange={set('google_client_id')} />
        </Field>
        <Field label={`Client secret ${s.google_client_secret_enc?.configured ? '(configured — enter to replace)' : ''}`}>
          <input className="input" type="password" value={f.google_client_secret} onChange={set('google_client_secret')} />
        </Field>
      </div>

      <div className="lg:col-span-2">
        <button className="btn-primary" disabled={save.isPending} onClick={submit}>Save settings</button>
      </div>
    </div>
  )
}

function ScanHistory() {
  const scans = useQuery({
    queryKey: ['scans', 'history'],
    queryFn: () => api.get('/api/scans?limit=100'),
    refetchInterval: 10_000,
  })
  if (scans.isLoading) return <Spinner />
  return (
    <div className="card">
      <table className="w-full">
        <thead><tr className="border-b border-slate-100">
          <th className="th">#</th><th className="th">Kind</th><th className="th">Trigger</th>
          <th className="th">Status</th><th className="th">Finished</th><th className="th">Results</th>
        </tr></thead>
        <tbody className="divide-y divide-slate-100">
          {scans.data.map((r) => (
            <tr key={r.id}>
              <td className="td text-slate-400">{r.id}</td>
              <td className="td capitalize">{r.kind}</td>
              <td className="td text-sm">{r.trigger}</td>
              <td className="td"><StatusBadge status={r.status} /></td>
              <td className="td text-xs">{fmtDate(r.finished_at)}</td>
              <td className="td text-xs text-slate-500">
                {r.error_text
                  ? <span className="text-rose-600">{r.error_text}</span>
                  : Object.entries(r.stats || {}).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(', ') || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Audit() {
  const [table, setTable] = useState('')
  const audit = useQuery({
    queryKey: ['audit', table],
    queryFn: () => api.get(`/api/admin/audit?limit=100${table ? `&table=${table}` : ''}`),
  })
  return (
    <div className="space-y-3">
      <select className="input w-56" value={table} onChange={(e) => setTable(e.target.value)}>
        <option value="">All tables</option>
        {['records', 'boxes', 'users', 'sources', 'projects', 'record_types', 'alerts'].map((t) => (
          <option key={t} value={t}>{t}</option>
        ))}
      </select>
      {audit.isLoading ? <Spinner /> : (
        <div className="card">
          <table className="w-full">
            <thead><tr className="border-b border-slate-100">
              <th className="th">When</th><th className="th">Who</th><th className="th">Table</th>
              <th className="th">Row</th><th className="th">Action</th><th className="th">Changed</th>
            </tr></thead>
            <tbody className="divide-y divide-slate-100">
              {audit.data.map((a) => (
                <tr key={a.id}>
                  <td className="td text-xs">{fmtDate(a.at)}</td>
                  <td className="td text-sm font-medium">{a.username}</td>
                  <td className="td text-sm">{a.table_name}</td>
                  <td className="td text-sm text-slate-500">#{a.row_pk}</td>
                  <td className="td"><Badge tone={a.action === 'INSERT' ? 'green' : 'blue'}>{a.action}</Badge></td>
                  <td className="td max-w-md truncate text-xs text-slate-500">
                    {a.changed_cols?.join(', ') || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
