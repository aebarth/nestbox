import { useQuery } from '@tanstack/react-query'
import { Download, FileText } from 'lucide-react'
import { Spinner, fmtDate } from '../components/ui.jsx'
import { api } from '../services/api.js'

const REPORTS = [
  { name: 'all-records', title: 'All records', desc: 'Every tracked record with its categorization, box and retention date.' },
  { name: 'missing', title: 'Missing files', desc: 'Records whose file could not be found at the last scan.' },
  { name: 'duplicates', title: 'Duplicates', desc: 'Files flagged as duplicates, grouped.' },
  { name: 'uncategorized', title: 'Uncategorized', desc: 'Records still missing a project or record type.' },
  { name: 'retention-due', title: 'Retention due', desc: 'Records whose retention period ends within 12 months.' },
  { name: 'box-inventory', title: 'Box inventory', desc: 'Every boxed record, grouped by box.' },
]

export default function Reports() {
  const summary = useQuery({
    queryKey: ['status-summary'],
    queryFn: () => api.get('/api/reports/status-summary'),
  })

  if (summary.isLoading) return <Spinner />
  const s = summary.data || {}

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold tracking-tight">Reports</h1>

      <div className="card p-5 text-sm text-slate-600">
        <span className="font-semibold text-slate-800">Current status:</span>{' '}
        {Object.entries(s.by_status || {}).map(([k, v]) => `${v} ${k}`).join(' · ') || 'no records yet'}
        {s.last_scan && <> · last scan finished {fmtDate(s.last_scan.finished_at)}</>}
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {REPORTS.map((r) => (
          <div key={r.name} className="card flex flex-col p-5">
            <div className="mb-1 flex items-center gap-2">
              <FileText size={17} className="text-amber-500" />
              <h2 className="font-semibold">{r.title}</h2>
            </div>
            <p className="flex-1 text-sm text-slate-600">{r.desc}</p>
            <div className="mt-4 flex gap-2">
              <a className="btn-secondary" href={`/api/reports/${r.name}.csv`}>
                <Download size={14} /> CSV
              </a>
              <a className="btn-secondary" href={`/api/reports/${r.name}.pdf`} target="_blank" rel="noreferrer">
                <Download size={14} /> PDF
              </a>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
