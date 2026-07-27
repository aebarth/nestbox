import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Copy, FileText, Inbox, Package, Play, SearchX } from 'lucide-react'
import { Link } from 'react-router-dom'
import { SeverityBadge, Spinner, StatusBadge, fmtDate, useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

function Stat({ icon: Icon, label, value, to, tone = 'text-slate-700' }) {
  return (
    <Link to={to} className="card flex items-center gap-4 p-5 transition-shadow hover:shadow-md">
      <div className="rounded-lg bg-slate-100 p-3"><Icon size={22} className="text-slate-500" /></div>
      <div>
        <div className={`text-2xl font-bold ${tone}`}>{value ?? 0}</div>
        <div className="text-sm text-slate-500">{label}</div>
      </div>
    </Link>
  )
}

export default function Dashboard() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const summary = useQuery({
    queryKey: ['status-summary'],
    queryFn: () => api.get('/api/reports/status-summary'),
    refetchInterval: 30_000,
  })
  const alerts = useQuery({
    queryKey: ['alerts', 'dashboard'],
    queryFn: () => api.get('/api/alerts?status=open&limit=8'),
  })
  const scans = useQuery({
    queryKey: ['scans', 'recent'],
    queryFn: () => api.get('/api/scans?limit=5'),
    refetchInterval: 15_000,
  })
  const scanNow = useMutation({
    mutationFn: () => api.post('/api/scans'),
    onSuccess: () => {
      toast('Scan queued — the worker will pick it up within a few seconds.')
      queryClient.invalidateQueries({ queryKey: ['scans'] })
    },
    onError: (e) => toast(e.message, 'error'),
  })

  if (summary.isLoading) return <Spinner />
  const s = summary.data || {}
  const openAlertCount = Object.values(s.open_alerts || {}).reduce((a, b) => a + b, 0)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
        <button className="btn-primary" onClick={() => scanNow.mutate()} disabled={scanNow.isPending}>
          <Play size={15} /> Scan all sources now
        </button>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
        <Stat icon={FileText} label="Active records" value={s.by_status?.active} to="/files" />
        <Stat icon={Inbox} label="Inbox (to file)" value={s.by_status?.inbox} to="/wizard" tone="text-sky-600" />
        <Stat icon={SearchX} label="Missing" value={s.by_status?.missing} to="/files?status=missing"
              tone={s.by_status?.missing ? 'text-rose-600' : 'text-slate-700'} />
        <Stat icon={AlertTriangle} label="Open alerts" value={openAlertCount} to="/alerts"
              tone={openAlertCount ? 'text-amber-600' : 'text-slate-700'} />
        <Stat icon={Copy} label="Duplicate groups" value={s.duplicate_groups} to="/files?duplicates_only=true" />
        <Stat icon={Package} label="Boxes" value={s.boxes} to="/boxes" />
      </div>

      {s.uncategorized > 0 && (
        <Link to="/onboarding"
              className="card flex items-center gap-4 border-amber-200 bg-amber-50 p-4 transition-shadow hover:shadow-md">
          <div className="rounded-lg bg-amber-100 p-2.5 text-amber-600"><FileText size={20} /></div>
          <div className="flex-1">
            <div className="font-medium text-amber-900">
              {s.uncategorized} record{s.uncategorized === 1 ? '' : 's'} without a record type
            </div>
            <div className="text-sm text-amber-700">
              Use Auto-categorize to map folders to record types in bulk — no hand-tagging needed.
            </div>
          </div>
          <span className="btn-primary">Auto-categorize</span>
        </Link>
      )}
      {s.ocr_pending > 0 && (
        <p className="text-xs text-slate-500">
          Text extraction in progress: {s.ocr_pending} document{s.ocr_pending === 1 ? '' : 's'} queued
          (content search and content-duplicate detection improve as this completes).
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="card p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">Latest alerts</h2>
            <Link to="/alerts" className="text-sm text-amber-600 hover:underline">View all</Link>
          </div>
          {alerts.data?.length ? (
            <ul className="divide-y divide-slate-100">
              {alerts.data.map((a) => (
                <li key={a.id} className="flex items-center gap-3 py-2.5">
                  <SeverityBadge severity={a.severity} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{a.record_name}</div>
                    <div className="truncate text-xs text-slate-500">
                      {a.alert_type} · {a.source_name} · {fmtDate(a.created_at)}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="py-6 text-center text-sm text-slate-500">No open alerts. All quiet. 🎉</p>
          )}
        </div>

        <div className="card p-5">
          <h2 className="mb-3 font-semibold">Recent scans</h2>
          {scans.data?.length ? (
            <ul className="divide-y divide-slate-100">
              {scans.data.map((r) => (
                <li key={r.id} className="flex items-center gap-3 py-2.5 text-sm">
                  <StatusBadge status={r.status} />
                  <span className="capitalize">{r.kind}</span>
                  <span className="text-xs text-slate-500">{r.trigger}</span>
                  <span className="ml-auto text-xs text-slate-500">
                    {fmtDate(r.finished_at || r.started_at || r.created_at)}
                  </span>
                  {r.stats && Object.keys(r.stats).length > 0 && (
                    <span className="text-xs text-slate-400">
                      {Object.entries(r.stats).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(', ')}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="py-6 text-center text-sm text-slate-500">
              No scans yet. Add a source in Admin, then press “Scan all sources now”.
            </p>
          )}
        </div>
      </div>

      <div className="card p-5">
        <h2 className="mb-3 font-semibold">Records by source</h2>
        <div className="flex flex-wrap gap-3">
          {Object.entries(s.by_source || {}).map(([name, count]) => (
            <div key={name} className="rounded-lg bg-slate-50 px-4 py-2 text-sm">
              <span className="font-semibold">{count}</span> in {name}
            </div>
          ))}
          {!Object.keys(s.by_source || {}).length && (
            <p className="text-sm text-slate-500">Nothing scanned yet.</p>
          )}
        </div>
      </div>
    </div>
  )
}
