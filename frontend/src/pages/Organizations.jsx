import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Plus, UserPlus } from 'lucide-react'
import { useState } from 'react'
import { Badge, Field, Modal, Spinner, fmtDate, useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

const ROLE_OPTIONS = ['owner', 'admin', 'member']

export default function Organizations() {
  const queryClient = useQueryClient()
  const orgs = useQuery({ queryKey: ['orgs'], queryFn: () => api.get('/api/admin/orgs') })
  const [creating, setCreating] = useState(false)
  const [selectedId, setSelectedId] = useState(null)

  if (selectedId) {
    return <OrgDetail orgId={selectedId} onBack={() => setSelectedId(null)} />
  }

  if (orgs.isLoading) return <Spinner />

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Organizations</h1>
        <button className="btn-primary" onClick={() => setCreating(true)}>
          <Plus size={14} /> New organization
        </button>
      </div>
      <p className="text-sm text-slate-500">
        Each organization is fully isolated — its files, projects and records are invisible to every other org.
      </p>
      <div className="card overflow-hidden">
        <table className="w-full">
          <thead><tr className="border-b border-slate-100">
            <th className="th">Name</th><th className="th">Type</th><th className="th">Members</th>
            <th className="th">Status</th><th className="th">Created</th><th className="th" />
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {orgs.data.map((o) => (
              <tr key={o.id} className={o.is_active ? '' : 'opacity-50'}>
                <td className="td font-medium">{o.name}</td>
                <td className="td"><Badge tone={o.kind === 'business' ? 'violet' : 'blue'}>{o.kind}</Badge></td>
                <td className="td">{o.member_count}</td>
                <td className="td"><Badge tone={o.is_active ? 'green' : 'slate'}>{o.is_active ? 'active' : 'inactive'}</Badge></td>
                <td className="td text-xs">{fmtDate(o.created_at)}</td>
                <td className="td text-right">
                  <button className="btn-secondary" onClick={() => setSelectedId(o.id)}>Manage</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {creating && <CreateOrgModal onClose={() => setCreating(false)} />}
    </div>
  )
}

function CreateOrgModal({ onClose }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [form, setForm] = useState({ name: '', kind: 'personal' })
  const save = useMutation({
    mutationFn: () => api.post('/api/admin/orgs', form),
    onSuccess: () => { toast('Organization created'); queryClient.invalidateQueries({ queryKey: ['orgs'] }); onClose() },
    onError: (e) => toast(e.message, 'error'),
  })
  return (
    <Modal title="New organization" onClose={onClose}>
      <div className="space-y-3">
        <Field label="Name">
          <input className="input" value={form.name} autoFocus
                 onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <Field label="Type" hint="Business for a shared team workspace; personal for individual use.">
          <select className="input" value={form.kind}
                  onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            <option value="personal">Personal</option>
            <option value="business">Business</option>
          </select>
        </Field>
        <button className="btn-primary w-full" disabled={!form.name || save.isPending}
                onClick={() => save.mutate()}>Create organization</button>
      </div>
    </Modal>
  )
}

function OrgDetail({ orgId, onBack }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const detail = useQuery({ queryKey: ['orgs', orgId], queryFn: () => api.get(`/api/admin/orgs/${orgId}`) })
  const [addingMember, setAddingMember] = useState(false)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['orgs'] })

  const updateOrg = useMutation({
    mutationFn: (body) => api.patch(`/api/admin/orgs/${orgId}`, body),
    onSuccess: () => { toast('Organization updated'); invalidate() },
    onError: (e) => toast(e.message, 'error'),
  })

  if (detail.isLoading) return <Spinner />
  const org = detail.data

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="text-sm text-slate-500 hover:text-slate-800">← All organizations</button>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Building2 size={26} className="text-slate-400" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{org.name}</h1>
            <p className="text-sm text-slate-500 capitalize">{org.kind} · /{org.slug}</p>
          </div>
        </div>
        <button className={org.is_active ? 'btn-secondary' : 'btn-primary'} disabled={updateOrg.isPending}
                onClick={() => updateOrg.mutate({ is_active: !org.is_active })}>
          {org.is_active ? 'Deactivate' : 'Activate'}
        </button>
      </div>

      <div className="card overflow-hidden">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <h2 className="font-semibold">Members</h2>
          <button className="btn-primary" onClick={() => setAddingMember(true)}>
            <UserPlus size={14} /> Add member
          </button>
        </div>
        <table className="w-full">
          <thead><tr className="border-b border-slate-100">
            <th className="th">Username</th><th className="th">Name</th><th className="th">Role</th>
            <th className="th">Status</th><th className="th" />
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {org.members.map((m) => (
              <MemberRow key={m.user_id} orgId={orgId} member={m} onChanged={invalidate} />
            ))}
          </tbody>
        </table>
      </div>

      {addingMember && <AddMemberModal orgId={orgId} onClose={() => setAddingMember(false)} />}
    </div>
  )
}

function MemberRow({ orgId, member, onChanged }) {
  const toast = useToast()
  const changeRole = useMutation({
    mutationFn: (role) => api.patch(`/api/admin/orgs/${orgId}/members/${member.user_id}`, { role }),
    onSuccess: () => { toast('Role updated'); onChanged() },
    onError: (e) => toast(e.message, 'error'),
  })
  const remove = useMutation({
    mutationFn: () => api.del(`/api/admin/orgs/${orgId}/members/${member.user_id}`),
    onSuccess: () => { toast('Member removed'); onChanged() },
    onError: (e) => toast(e.message, 'error'),
  })
  return (
    <tr className={member.is_active ? '' : 'opacity-50'}>
      <td className="td font-medium">{member.username}</td>
      <td className="td">{member.full_name || '—'}</td>
      <td className="td">
        <select className="input !w-auto" value={member.role} disabled={changeRole.isPending}
                onChange={(e) => changeRole.mutate(e.target.value)}>
          {ROLE_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </td>
      <td className="td"><Badge tone={member.is_active ? 'green' : 'red'}>{member.is_active ? 'active' : 'disabled'}</Badge></td>
      <td className="td text-right">
        <button className="btn-secondary !text-rose-600" disabled={remove.isPending}
                onClick={() => remove.mutate()}>Remove</button>
      </td>
    </tr>
  )
}

function AddMemberModal({ orgId, onClose }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const users = useQuery({ queryKey: ['users'], queryFn: () => api.get('/api/admin/users') })
  const [form, setForm] = useState({ user_id: '', role: 'member' })
  const save = useMutation({
    mutationFn: () => api.post(`/api/admin/orgs/${orgId}/members`, { user_id: Number(form.user_id), role: form.role }),
    onSuccess: () => { toast('Member added'); queryClient.invalidateQueries({ queryKey: ['orgs'] }); onClose() },
    onError: (e) => toast(e.message, 'error'),
  })
  return (
    <Modal title="Add member" onClose={onClose}>
      <div className="space-y-3">
        <Field label="User">
          <select className="input" value={form.user_id}
                  onChange={(e) => setForm({ ...form, user_id: e.target.value })}>
            <option value="">Select a user…</option>
            {users.data?.map((u) => (
              <option key={u.id} value={u.id}>{u.username}{u.full_name ? ` — ${u.full_name}` : ''}</option>
            ))}
          </select>
        </Field>
        <Field label="Role">
          <select className="input" value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {ROLE_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </Field>
        <button className="btn-primary w-full" disabled={!form.user_id || save.isPending}
                onClick={() => save.mutate()}>Add member</button>
      </div>
    </Modal>
  )
}
