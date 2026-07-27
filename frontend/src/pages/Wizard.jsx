import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronRight, Inbox, Printer, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { EmptyState, Field, Spinner, fmtBytes, fmtDate, useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

const STEPS = ['Pick a scan', 'Name & categorize', 'Print label', 'Assign to box', 'File it']

const EMPTY_FORM = {
  title: '', project_id: null, record_type_id: null, lot_number: '', part_number: '',
  tags: [], notes: '', box_id: null, destination_source_id: null, destination_folder: '',
  new_filename: '',
}

export default function Wizard() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState(null)
  const [step, setStep] = useState(0)
  const [form, setForm] = useState(EMPTY_FORM)
  const [done, setDone] = useState(null)

  const inbox = useQuery({
    queryKey: ['wizard-inbox'],
    queryFn: () => api.get('/api/wizard/inbox'),
    refetchInterval: 20_000,
  })
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get('/api/lookups/projects') })
  const types = useQuery({ queryKey: ['record-types'], queryFn: () => api.get('/api/lookups/record-types') })
  const boxes = useQuery({ queryKey: ['boxes'], queryFn: () => api.get('/api/boxes') })
  const sources = useQuery({ queryKey: ['sources'], queryFn: () => api.get('/api/sources') })
  const scanNow = useMutation({
    mutationFn: () => api.post('/api/scans'),
    onSuccess: () => toast('Scan queued — new PDFs in the inbox folder will appear shortly.'),
  })

  const destinations = sources.data?.filter((s) => s.is_active && !s.config?.builtin_inbox) || []

  const fileIt = useMutation({
    mutationFn: () => api.post(`/api/wizard/${selected.id}/file`, {
      ...form,
      new_filename: form.new_filename || undefined,
    }),
    onSuccess: (r) => {
      setDone(r)
      queryClient.invalidateQueries({ queryKey: ['wizard-inbox'] })
      queryClient.invalidateQueries({ queryKey: ['records'] })
    },
    onError: (e) => toast(e.message, 'error'),
  })

  async function printLabel() {
    const resp = await fetch('/api/labels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record_ids: [selected.id], format: 'avery5160' }),
    })
    if (!resp.ok) { toast('Label failed', 'error'); return }
    window.open(URL.createObjectURL(await resp.blob()), '_blank')
  }

  function pick(record) {
    setSelected(record)
    setForm({ ...EMPTY_FORM, title: record.title || record.name.replace(/\.pdf$/i, '') })
    setStep(1)
    setDone(null)
  }

  function reset() {
    setSelected(null)
    setStep(0)
    setDone(null)
    setForm(EMPTY_FORM)
  }

  const canFile = form.title && form.destination_source_id

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Scan to Shelf</h1>
        <button className="btn-secondary" onClick={() => scanNow.mutate()}>
          <RefreshCw size={14} /> Check inbox folder
        </button>
      </div>

      <ol className="flex flex-wrap items-center gap-1 text-sm">
        {STEPS.map((label, i) => (
          <li key={label} className="flex items-center gap-1">
            <span className={`flex items-center gap-1.5 rounded-full px-3 py-1 ${
              i < step ? 'bg-emerald-100 text-emerald-700'
              : i === step ? 'bg-amber-500 text-white'
              : 'bg-slate-100 text-slate-400'
            }`}>
              {i < step ? <Check size={13} /> : <span className="text-xs font-bold">{i + 1}</span>}
              {label}
            </span>
            {i < STEPS.length - 1 && <ChevronRight size={14} className="text-slate-300" />}
          </li>
        ))}
      </ol>

      {done ? (
        <div className="card p-8 text-center">
          <Check size={40} className="mx-auto mb-3 text-emerald-500" />
          <h2 className="text-lg font-semibold">Filed!</h2>
          <p className="mt-1 text-sm text-slate-600">
            <span className="font-medium">{done.title}</span> is now at{' '}
            <span className="font-mono text-xs">{done.source_name}: {done.path}</span>
            {done.box_number && <> · Box <span className="font-medium">{done.box_number}</span></>}
          </p>
          <button className="btn-primary mt-5" onClick={reset}>File the next one</button>
        </div>
      ) : step === 0 ? (
        <div className="card">
          {inbox.isLoading ? <Spinner /> : !inbox.data?.length ? (
            <EmptyState icon={Inbox} title="The scan inbox is empty">
              Scan a paper record to your inbox folder (the NESTBOX_INBOX_PATH folder), then press
              “Check inbox folder”. New PDFs appear here ready to file.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-slate-100">
              {inbox.data.map((r) => (
                <li key={r.id} className="flex cursor-pointer items-center gap-4 px-5 py-3 hover:bg-amber-50/40"
                    onClick={() => pick(r)}>
                  <Inbox size={18} className="shrink-0 text-sky-500" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{r.name}</div>
                    <div className="text-xs text-slate-500">
                      {fmtBytes(r.size_bytes)} · scanned {fmtDate(r.file_mtime || r.first_seen_at)}
                    </div>
                  </div>
                  <ChevronRight size={16} className="text-slate-300" />
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div className="card space-y-4 p-6">
          <div className="rounded-lg bg-slate-50 p-3 text-sm">
            <span className="text-slate-500">Filing:</span>{' '}
            <span className="font-medium">{selected.name}</span>{' '}
            <span className="text-xs text-slate-400">({fmtBytes(selected.size_bytes)})</span>
          </div>

          {step === 1 && (
            <div className="space-y-3">
              <Field label="Record title" hint="A human-readable name for this record.">
                <input className="input" value={form.title} autoFocus
                       onChange={(e) => setForm({ ...form, title: e.target.value })} />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Project">
                  <select className="input" value={form.project_id ?? ''}
                          onChange={(e) => setForm({ ...form, project_id: e.target.value ? +e.target.value : null })}>
                    <option value="">—</option>
                    {projects.data?.filter((p) => p.is_active).map((p) => (
                      <option key={p.id} value={p.id}>{p.code} — {p.name}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Record type">
                  <select className="input" value={form.record_type_id ?? ''}
                          onChange={(e) => setForm({ ...form, record_type_id: e.target.value ? +e.target.value : null })}>
                    <option value="">—</option>
                    {types.data?.filter((t) => t.is_active).map((t) => (
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
              </div>
              <Field label="Notes">
                <textarea className="input" rows={2} value={form.notes}
                          onChange={(e) => setForm({ ...form, notes: e.target.value })} />
              </Field>
            </div>
          )}

          {step === 2 && (
            <div className="py-4 text-center">
              <p className="mb-4 text-sm text-slate-600">
                Print a label with a QR code that links back to this record. Stick it on the
                physical document or folder. (Save the PDF and print onto Avery 5160 stock.)
              </p>
              <button className="btn-primary" onClick={printLabel}>
                <Printer size={15} /> Generate label PDF
              </button>
              <p className="mt-3 text-xs text-slate-400">You can also skip this and print later from the record.</p>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-3">
              <Field label="Box" hint="Which physical box does the paper copy go in? Create boxes on the Boxes page.">
                <select className="input" value={form.box_id ?? ''}
                        onChange={(e) => setForm({ ...form, box_id: e.target.value ? +e.target.value : null })}>
                  <option value="">No box / digital only</option>
                  {boxes.data?.filter((b) => b.status === 'open').map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.box_number} — {b.physical_location || 'no location'} ({b.record_count} records)
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          )}

          {step === 4 && (
            <div className="space-y-3">
              <Field label="Destination" hint="Where the PDF will be moved for permanent storage.">
                <select className="input" value={form.destination_source_id ?? ''}
                        onChange={(e) => setForm({ ...form, destination_source_id: e.target.value ? +e.target.value : null })}>
                  <option value="">Choose a location…</option>
                  {destinations.map((s) => (
                    <option key={s.id} value={s.id}>{s.name} ({s.kind === 'local' ? 'network/local' : s.kind === 'gdrive' ? 'Google Drive' : 'OneDrive/SharePoint'})</option>
                  ))}
                </select>
              </Field>
              <Field label="Folder inside destination" hint="Optional, e.g. 2026/DHR — folders are created as needed.">
                <input className="input" value={form.destination_folder} placeholder=""
                       onChange={(e) => setForm({ ...form, destination_folder: e.target.value })} />
              </Field>
              <Field label="Final file name" hint="Leave blank to keep the scanner's name.">
                <input className="input" value={form.new_filename} placeholder={selected.name}
                       onChange={(e) => setForm({ ...form, new_filename: e.target.value })} />
              </Field>
            </div>
          )}

          <div className="flex justify-between border-t border-slate-100 pt-4">
            <button className="btn-secondary" onClick={() => (step === 1 ? reset() : setStep(step - 1))}>
              Back
            </button>
            {step < 4 ? (
              <button className="btn-primary" disabled={step === 1 && !form.title}
                      onClick={() => setStep(step + 1)}>
                Continue <ChevronRight size={15} />
              </button>
            ) : (
              <button className="btn-primary" disabled={!canFile || fileIt.isPending}
                      onClick={() => fileIt.mutate()}>
                {fileIt.isPending ? 'Filing…' : 'Move file & finish'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
