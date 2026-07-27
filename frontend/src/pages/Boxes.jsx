import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Package, Plus, Printer } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { EmptyState, Field, Modal, Spinner, StatusBadge, useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

const EMPTY = { box_number: '', physical_location: '', description: '', status: 'open' }

export default function Boxes() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const boxes = useQuery({ queryKey: ['boxes'], queryFn: () => api.get('/api/boxes') })
  const [editing, setEditing] = useState(null) // {id?, ...form}

  const save = useMutation({
    mutationFn: (form) => form.id
      ? api.patch(`/api/boxes/${form.id}`, form)
      : api.post('/api/boxes', form),
    onSuccess: () => {
      toast('Box saved')
      setEditing(null)
      queryClient.invalidateQueries({ queryKey: ['boxes'] })
    },
    onError: (e) => toast(e.message, 'error'),
  })

  async function printLabels(box, contents) {
    const body = contents
      ? { box_contents_ids: [box.id], format: 'avery5160' }
      : { box_ids: [box.id], format: 'avery5163' }
    const resp = await fetch('/api/labels', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!resp.ok) { toast((await resp.json()).detail || 'Label failed', 'error'); return }
    window.open(URL.createObjectURL(await resp.blob()), '_blank')
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Boxes</h1>
        <button className="btn-primary" onClick={() => setEditing({ ...EMPTY })}>
          <Plus size={15} /> New box
        </button>
      </div>

      {boxes.isLoading ? <Spinner /> : !boxes.data?.length ? (
        <div className="card">
          <EmptyState icon={Package} title="No boxes yet">
            Create a box for physical records — give it a number and a shelf/room location, then
            assign records to it from the Files page or the Scan-to-Shelf wizard.
          </EmptyState>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {boxes.data.map((b) => (
            <div key={b.id} className="card p-5">
              <div className="mb-1 flex items-center justify-between">
                <h2 className="text-lg font-semibold">Box {b.box_number}</h2>
                <StatusBadge status={b.status} />
              </div>
              <p className="text-sm text-slate-600">{b.physical_location || 'No location set'}</p>
              {b.description && <p className="mt-1 text-xs text-slate-500">{b.description}</p>}
              <p className="mt-3 text-sm">
                <Link className="font-medium text-amber-600 hover:underline" to={`/files?box_id=${b.id}`}>
                  {b.record_count} record{b.record_count === 1 ? '' : 's'}
                </Link>
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <button className="btn-secondary" onClick={() => setEditing({ ...b })}>Edit</button>
                <button className="btn-secondary" onClick={() => printLabels(b, false)}>
                  <Printer size={14} /> Box label
                </button>
                {b.record_count > 0 && (
                  <button className="btn-secondary" onClick={() => printLabels(b, true)}>
                    <Printer size={14} /> Contents labels
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <Modal title={editing.id ? `Edit box ${editing.box_number}` : 'New box'} onClose={() => setEditing(null)}>
          <div className="space-y-3">
            <Field label="Box number" hint="Your physical label, e.g. QA-2026-014">
              <input className="input" value={editing.box_number}
                     onChange={(e) => setEditing({ ...editing, box_number: e.target.value })} />
            </Field>
            <Field label="Physical location" hint="Room, rack, shelf…">
              <input className="input" value={editing.physical_location}
                     onChange={(e) => setEditing({ ...editing, physical_location: e.target.value })} />
            </Field>
            <Field label="Description">
              <input className="input" value={editing.description}
                     onChange={(e) => setEditing({ ...editing, description: e.target.value })} />
            </Field>
            {editing.id && (
              <Field label="Status" hint="Sealed boxes can't take new records. Destroyed = contents shredded after retention.">
                <select className="input" value={editing.status}
                        onChange={(e) => setEditing({ ...editing, status: e.target.value })}>
                  <option value="open">open</option>
                  <option value="sealed">sealed</option>
                  <option value="destroyed">destroyed</option>
                </select>
              </Field>
            )}
            <button className="btn-primary w-full" disabled={!editing.box_number || save.isPending}
                    onClick={() => save.mutate(editing)}>
              Save box
            </button>
          </div>
        </Modal>
      )}
    </div>
  )
}
