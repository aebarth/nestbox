import { X } from 'lucide-react'
import { createContext, useCallback, useContext, useState } from 'react'

export function Badge({ tone = 'slate', children }) {
  const tones = {
    slate: 'bg-slate-100 text-slate-700',
    green: 'bg-emerald-100 text-emerald-700',
    amber: 'bg-amber-100 text-amber-800',
    red: 'bg-rose-100 text-rose-700',
    blue: 'bg-sky-100 text-sky-700',
    violet: 'bg-violet-100 text-violet-700',
  }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone] || tones.slate}`}>
      {children}
    </span>
  )
}

export function StatusBadge({ status }) {
  const map = {
    inbox: ['blue', 'Inbox'],
    active: ['green', 'Active'],
    missing: ['red', 'Missing'],
    retired: ['slate', 'Retired'],
    superseded: ['violet', 'Superseded'],
    open: ['red', 'Open'],
    acknowledged: ['amber', 'Acknowledged'],
    resolved: ['green', 'Resolved'],
    queued: ['blue', 'Queued'],
    running: ['amber', 'Running'],
    done: ['green', 'Done'],
    error: ['red', 'Error'],
    sealed: ['amber', 'Sealed'],
    destroyed: ['slate', 'Destroyed'],
  }
  const [tone, label] = map[status] || ['slate', status]
  return <Badge tone={tone}>{label}</Badge>
}

export function SeverityBadge({ severity }) {
  const map = { critical: 'red', warning: 'amber', info: 'blue' }
  return <Badge tone={map[severity] || 'slate'}>{severity}</Badge>
}

export function Modal({ title, onClose, children, wide }) {
  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-slate-900/50 p-4 pt-16"
         onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`card max-h-[85vh] w-full overflow-y-auto p-5 ${wide ? 'max-w-3xl' : 'max-w-lg'}`}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

export function Field({ label, children, hint }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

export function EmptyState({ icon: Icon, title, children }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      {Icon && <Icon size={40} className="mb-3 text-slate-300" />}
      <p className="font-medium text-slate-600">{title}</p>
      {children && <p className="mt-1 max-w-md text-sm text-slate-500">{children}</p>}
    </div>
  )
}

export function Spinner({ label = 'Loading…' }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-slate-500">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-amber-500" />
      <span className="text-sm">{label}</span>
    </div>
  )
}

const ToastContext = createContext(() => {})

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const push = useCallback((message, tone = 'ok') => {
    const id = Math.random().toString(36).slice(2)
    setToasts((t) => [...t, { id, message, tone }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500)
  }, [])
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id}
               className={`pointer-events-auto rounded-lg px-4 py-2.5 text-sm text-white shadow-lg ${
                 t.tone === 'error' ? 'bg-rose-600' : 'bg-slate-800'
               }`}>
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)

export function fmtBytes(n) {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

export function fmtDate(s) {
  if (!s) return '—'
  return new Date(s).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}
