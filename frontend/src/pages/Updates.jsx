import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpCircle, Check, Copy, Package, RefreshCw, ShieldCheck } from 'lucide-react'
import { api, qs } from '../services/api.js'

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—'
}

export default function Updates() {
  const queryClient = useQueryClient()
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [verifyResult, setVerifyResult] = useState(null) // 'success' | 'mismatch'

  // Current version (always loaded on page mount)
  const version = useQuery({
    queryKey: ['version'],
    queryFn: () => api.get('/api/admin/version'),
  })

  // Check for updates (triggered by button, or auto on first visit)
  const check = useQuery({
    queryKey: ['updates-check'],
    queryFn: () => api.get(`/api/admin/updates/check${qs({ force: true })}`),
    staleTime: 5 * 60 * 1000, // match server-side cache TTL
  })

  // Prepare update (creates backup + returns commands)
  const prepare = useMutation({
    mutationFn: () => api.post('/api/admin/updates/prepare'),
    onSuccess: (data) => { setError(''); queryClient.invalidateQueries({ queryKey: ['backups'] }) },
    onError: (e) => setError(e.message),
  })

  const copyCommand = (text) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  // Post-restart verify: re-fetch version and compare to the pinned target
  const handleVerify = async () => {
    setVerifying(true)
    setVerifyResult(null)
    try {
      const fresh = await api.get('/api/admin/version')
      const target = prepare.data?.pinned_version
      if (target && fresh.installed_version === target) {
        setVerifyResult('success')
      } else {
        setVerifyResult('mismatch')
      }
    } catch {
      setVerifyResult('mismatch')
    } finally {
      setVerifying(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* Header */}
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold text-slate-900">
          <Package size={20} className="text-emerald-500" /> Updates
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Check for new versions and prepare a guided update. A backup is always taken before you apply changes.
        </p>
      </div>

      {/* Error banner */}
      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700" role="alert">
          {error}
          <button onClick={() => setError('')} className="ml-3 text-xs underline">dismiss</button>
        </div>
      )}

      {/* Current version card */}
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-800">Current version</h2>
        {version.isLoading ? (
          <p className="mt-2 text-sm text-slate-400">Loading…</p>
        ) : version.data ? (
          <div className="mt-3 flex items-baseline gap-4">
            <span className="text-2xl font-bold text-slate-900">{version.data.installed_version}</span>
            <span className="text-xs text-slate-400">schema v{version.data.schema_version}</span>
          </div>
        ) : (
          <p className="mt-2 text-sm text-rose-600">Could not load version.</p>
        )}
      </section>

      {/* Check for updates */}
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-800">Check for updates</h2>
          <button
            onClick={() => check.refetch()}
            disabled={check.isFetching}
            className="flex items-center gap-1.5 rounded-lg bg-slate-100 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-200 disabled:opacity-50"
          >
            <RefreshCw size={14} className={check.isFetching ? 'animate-spin' : ''} />
            {check.isFetching ? 'Checking…' : 'Check now'}
          </button>
        </div>

        {check.isLoading || check.isFetching ? (
          <p className="mt-3 text-sm text-slate-400">Contacting GitHub…</p>
        ) : check.error ? (
          <p className="mt-3 text-sm text-rose-600">{check.error.message}</p>
        ) : check.data ? (
          <div className="mt-3 space-y-3">
            {/* Status line */}
            {check.data.update_available ? (
              <div className="flex items-center gap-2 rounded-lg bg-emerald-50 px-4 py-3">
                <ArrowUpCircle size={18} className="text-emerald-600" />
                <div>
                  <p className="text-sm font-medium text-emerald-800">
                    Update available: {check.data.latest_version}
                  </p>
                  <p className="text-xs text-emerald-600">Published {fmtDate(check.data.published_at)}</p>
                </div>
              </div>
            ) : check.data.latest_version ? (
              <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-4 py-3">
                <Check size={18} className="text-slate-400" />
                <p className="text-sm text-slate-600">
                  You are on the latest version ({check.data.latest_version}).
                </p>
              </div>
            ) : (
              <p className="text-sm text-slate-500">No releases found on GitHub.</p>
            )}

            {/* Release notes */}
            {check.data.release_notes && (
              <details className="rounded-lg border border-slate-100 bg-slate-50/50 px-4 py-3">
                <summary className="cursor-pointer text-xs font-medium text-slate-600 hover:text-slate-800">
                  Release notes
                </summary>
                <p className="mt-2 whitespace-pre-wrap text-xs text-slate-600">{check.data.release_notes}</p>
              </details>
            )}

            {/* Prepare button (only when update available) */}
            {check.data.update_available && (
              <button
                onClick={() => prepare.mutate()}
                disabled={prepare.isPending}
                className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                <ArrowUpCircle size={16} />
                {prepare.isPending ? 'Preparing (taking backup)…' : `Prepare update to ${check.data.latest_version}`}
              </button>
            )}
          </div>
        ) : null}
      </section>

      {/* Prepared update: commands to run */}
      {prepare.data && (
        <section className="rounded-xl border border-emerald-200 bg-emerald-50/40 p-5">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-emerald-800">
            <Check size={16} /> Update prepared
          </h2>
          <p className="mt-1 text-xs text-emerald-700/80">
            A backup was created ({prepare.data.backup.filename}). Run these commands on the host to apply the update.
          </p>

          {/* Commands */}
          <div className="mt-3 space-y-2" data-testid="update-commands">
            {prepare.data.commands.map((cmd, i) => (
              <div key={i} className="group relative">
                <pre className="overflow-x-auto rounded-lg bg-slate-900 px-4 py-3 pr-16 font-mono text-xs text-emerald-300">
                  {cmd}
                </pre>
                <button
                  onClick={() => copyCommand(cmd)}
                  className="absolute right-2 top-2 rounded p-1 text-slate-400 opacity-0 transition-opacity group-hover:opacity-100 hover:text-white"
                  title="Copy command"
                >
                  {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                </button>
              </div>
            ))}
          </div>

          {/* Pinned version */}
          {prepare.data.pinned_version && (
            <p className="mt-3 text-xs text-slate-500">
              Pinned to <code className="rounded bg-slate-100 px-1 py-0.5 font-mono">{prepare.data.pinned_version}</code> —
              after restart, verify the new version appears in this panel.
            </p>
          )}

          {/* Post-restart verify step */}
          <div className="mt-4 rounded-lg border border-slate-200 bg-white px-4 py-3">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-medium text-slate-700">Verify update</p>
                <p className="mt-0.5 text-[11px] text-slate-500">
                  After running the commands above and the app restarts, confirm the new version is active.
                </p>
              </div>
              <button
                onClick={handleVerify}
                disabled={verifying}
                className="flex items-center gap-1.5 rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-50"
              >
                <ShieldCheck size={14} className={verifying ? 'animate-pulse' : ''} />
                {verifying ? 'Checking…' : 'Verify now'}
              </button>
            </div>

            {verifyResult === 'success' && (
              <div className="mt-3 flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2">
                <Check size={14} className="text-emerald-600" />
                <p className="text-xs font-medium text-emerald-800">
                  Update verified — running {prepare.data.pinned_version}. Migrations applied successfully.
                </p>
              </div>
            )}
            {verifyResult === 'mismatch' && (
              <div className="mt-3 flex items-center gap-2 rounded-lg bg-amber-50 px-3 py-2">
                <RefreshCw size={14} className="text-amber-600" />
                <p className="text-xs text-amber-800">
                  Version mismatch — the app may still be restarting, or the update did not apply.
                  Check the host logs and try again in a moment.
                </p>
              </div>
            )}
          </div>

          {/* Rollback hint */}
          <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-xs font-medium text-amber-800">If something goes wrong:</p>
            <ol className="mt-1 list-inside list-decimal text-xs text-amber-700">
              <li>Pin the previous tag in <code className="font-mono">.env</code> (<code className="font-mono">NESTBOX_VERSION=&lt;old&gt;</code>)</li>
              <li>Run <code className="font-mono">docker compose up -d</code></li>
              <li>Restore the pre-update backup from Admin → Database</li>
            </ol>
          </div>
        </section>
      )}
    </div>
  )
}
