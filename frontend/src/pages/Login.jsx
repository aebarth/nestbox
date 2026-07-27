import { useQueryClient } from '@tanstack/react-query'
import { Archive } from 'lucide-react'
import { useState } from 'react'
import { Field, useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

export default function Login({ needsSetup }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const [form, setForm] = useState({ username: '', password: '', full_name: '', org_name: '' })
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    try {
      if (needsSetup) {
        await api.post('/api/auth/setup', form)
      } else {
        await api.post('/api/auth/login', { username: form.username, password: form.password })
      }
      await queryClient.invalidateQueries()
    } catch (err) {
      toast(err.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900 p-4">
      <form onSubmit={submit} className="card w-full max-w-md p-8">
        <div className="mb-6 flex items-center gap-3">
          <Archive className="text-amber-500" size={32} />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Nestbox</h1>
            <p className="text-sm text-slate-500">
              {needsSetup ? 'Welcome! Create the administrator account.' : 'Sign in to continue.'}
            </p>
          </div>
        </div>
        <div className="space-y-4">
          {needsSetup && (
            <>
              <Field label="Organization name">
                <input className="input" value={form.org_name} onChange={set('org_name')}
                       placeholder="Acme Medical Devices" />
              </Field>
              <Field label="Your name">
                <input className="input" value={form.full_name} onChange={set('full_name')} />
              </Field>
            </>
          )}
          <Field label="Username">
            <input className="input" value={form.username} onChange={set('username')}
                   autoComplete="username" required minLength={3} />
          </Field>
          <Field label="Password" hint={needsSetup ? 'At least 8 characters.' : undefined}>
            <input className="input" type="password" value={form.password} onChange={set('password')}
                   autoComplete={needsSetup ? 'new-password' : 'current-password'} required minLength={needsSetup ? 8 : 1} />
          </Field>
          <button className="btn-primary w-full" disabled={busy}>
            {needsSetup ? 'Create admin account' : 'Sign in'}
          </button>
        </div>
      </form>
    </div>
  )
}
