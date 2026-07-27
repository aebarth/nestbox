import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderTree, Wand2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, EmptyState, Spinner, useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

export default function Onboarding() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const sources = useQuery({ queryKey: ['sources'], queryFn: () => api.get('/api/sources') })
  const types = useQuery({ queryKey: ['record-types'], queryFn: () => api.get('/api/lookups/record-types') })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get('/api/lookups/projects') })
  const [sourceId, setSourceId] = useState(null)
  const [mappings, setMappings] = useState({}) // folder -> {record_type_id, project_id}
  const [saveRules, setSaveRules] = useState(true)
  const [overwrite, setOverwrite] = useState(false)

  const eligible = sources.data?.filter((s) => s.is_active && !s.config?.builtin_inbox) || []
  useEffect(() => {
    if (sourceId == null && eligible.length) setSourceId(eligible[0].id)
  }, [eligible, sourceId])

  const folders = useQuery({
    queryKey: ['onboarding-folders', sourceId],
    queryFn: () => api.get(`/api/onboarding/folders?source_id=${sourceId}`),
    enabled: sourceId != null,
  })

  // preload suggestions into the mapping state whenever folders load
  useEffect(() => {
    if (!folders.data) return
    const next = {}
    for (const f of folders.data) {
      next[f.folder] = {
        record_type_id: f.rule_record_type_id ?? f.suggested_record_type_id ?? null,
        project_id: f.rule_project_id ?? null,
      }
    }
    setMappings(next)
  }, [folders.data])

  const apply = useMutation({
    mutationFn: () => api.post('/api/onboarding/apply', {
      source_id: sourceId,
      mappings: Object.entries(mappings)
        .filter(([, m]) => m.record_type_id || m.project_id)
        .map(([folder, m]) => ({ folder, ...m })),
      overwrite,
      save_rules: saveRules,
    }),
    onSuccess: (r) => {
      toast(`Categorized ${r.updated_records} records${saveRules ? ' and saved the folder rules for future scans' : ''}.`)
      queryClient.invalidateQueries({ queryKey: ['onboarding-folders'] })
      queryClient.invalidateQueries({ queryKey: ['records'] })
      queryClient.invalidateQueries({ queryKey: ['status-summary'] })
    },
    onError: (e) => toast(e.message, 'error'),
  })

  const set = (folder, key, value) =>
    setMappings((m) => ({ ...m, [folder]: { ...m[folder], [key]: value } }))

  const pendingCount = Object.values(mappings).filter((m) => m.record_type_id || m.project_id).length

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Auto-categorize</h1>
        <p className="mt-1 max-w-2xl text-sm text-slate-600">
          Most organizations already keep records in folders named for the record type.
          Map each folder once — Nestbox categorizes everything inside it and (optionally)
          keeps the rule so newly scanned files are categorized automatically.
        </p>
      </div>

      <div className="card flex flex-wrap items-center gap-4 p-4">
        <select className="input w-72" value={sourceId ?? ''} onChange={(e) => setSourceId(+e.target.value)}>
          {eligible.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={saveRules} onChange={(e) => setSaveRules(e.target.checked)} />
          Keep as rules for future scans
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
          Overwrite existing categorization
        </label>
        <button className="btn-primary ml-auto" disabled={!pendingCount || apply.isPending}
                onClick={() => apply.mutate()}>
          <Wand2 size={15} /> Apply to {pendingCount} folder{pendingCount === 1 ? '' : 's'}
        </button>
      </div>

      {folders.isLoading ? <Spinner /> : !folders.data?.length ? (
        <div className="card">
          <EmptyState icon={FolderTree} title="No records in this source yet">
            Run a scan first (Dashboard → Scan all sources now), then come back here.
          </EmptyState>
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-slate-200 bg-slate-50">
              <tr>
                <th className="th">Folder</th>
                <th className="th">Files</th>
                <th className="th">Uncategorized</th>
                <th className="th">Record type</th>
                <th className="th">Project</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {folders.data.map((f) => {
                const m = mappings[f.folder] || {}
                return (
                  <tr key={f.folder}>
                    <td className="td font-mono text-xs">{f.folder || <span className="text-slate-400">(top level)</span>}</td>
                    <td className="td">{f.total}</td>
                    <td className="td">
                      {f.untyped > 0
                        ? <Badge tone="amber">{f.untyped}</Badge>
                        : <Badge tone="green">done</Badge>}
                    </td>
                    <td className="td">
                      <div className="flex items-center gap-2">
                        <select className="input w-56"
                                value={m.record_type_id ?? ''}
                                onChange={(e) => set(f.folder, 'record_type_id', e.target.value ? +e.target.value : null)}>
                          <option value="">—</option>
                          {types.data?.filter((t) => t.is_active).map((t) => (
                            <option key={t.id} value={t.id}>{t.code} — {t.name}</option>
                          ))}
                        </select>
                        {f.suggested_record_type_id && m.record_type_id === f.suggested_record_type_id
                          && !f.rule_record_type_id && (
                          <Badge tone="blue">suggested</Badge>
                        )}
                        {f.rule_record_type_id && <Badge tone="violet">rule</Badge>}
                      </div>
                    </td>
                    <td className="td">
                      <select className="input w-48"
                              value={m.project_id ?? ''}
                              onChange={(e) => set(f.folder, 'project_id', e.target.value ? +e.target.value : null)}>
                        <option value="">—</option>
                        {projects.data?.filter((p) => p.is_active).map((p) => (
                          <option key={p.id} value={p.id}>{p.code} — {p.name}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
