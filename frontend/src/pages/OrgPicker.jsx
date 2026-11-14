import { useQueryClient } from '@tanstack/react-query'
import { Archive, Building2, Check, User as UserIcon } from 'lucide-react'
import { useState } from 'react'
import { useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

export default function OrgPicker({ orgs }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const [busyId, setBusyId] = useState(null)

  async function choose(org) {
    setBusyId(org.id)
    try {
      await api.post('/api/auth/select-org', { org_id: org.id })
      await queryClient.invalidateQueries()
    } catch (err) {
      toast(err.message, 'error')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900 p-4">
      <div className="card w-full max-w-md p-8">
        <div className="mb-6 flex items-center gap-3">
          <Archive className="text-amber-500" size={32} />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Choose a workspace</h1>
            <p className="text-sm text-slate-500">You belong to more than one organization.</p>
          </div>
        </div>
        <div className="space-y-2">
          {orgs.map((o) => (
            <button key={o.id} onClick={() => choose(o)} disabled={busyId !== null}
                    className="flex w-full items-center gap-3 rounded-lg border border-slate-200 p-3 text-left transition-colors hover:border-amber-400 hover:bg-amber-50 disabled:opacity-60">
              {o.kind === 'business'
                ? <Building2 size={20} className="text-slate-400" />
                : <UserIcon size={20} className="text-slate-400" />}
              <span className="flex-1">
                <span className="block font-medium text-slate-800">{o.name}</span>
                <span className="block text-xs capitalize text-slate-500">{o.kind} · {o.role}</span>
              </span>
              {busyId === o.id
                ? <span className="text-xs text-slate-400">Loading…</span>
                : <Check size={18} className="text-slate-300" />}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
