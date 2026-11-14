import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  Bell,
  Building2,
  Database as DatabaseIcon,
  FileText,
  Inbox as InboxIcon,
  LayoutDashboard,
  LogOut,
  Package,
  ScanLine,
  Settings,
  FileBarChart,
  Wand2,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { useToast } from '../components/ui.jsx'
import { api } from '../services/api.js'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/files', label: 'Files', icon: FileText },
  { to: '/inbox', label: 'Review Inbox', icon: InboxIcon },
  { to: '/wizard', label: 'Scan to Shelf', icon: ScanLine },
  { to: '/boxes', label: 'Boxes', icon: Package },
  { to: '/alerts', label: 'Alerts', icon: Bell },
  { to: '/reports', label: 'Reports', icon: FileBarChart },
]

export default function Layout({ user, children }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const alerts = useQuery({
    queryKey: ['alerts', 'open-count'],
    queryFn: () => api.get('/api/alerts?status=unresolved&limit=500'),
    refetchInterval: 60_000,
  })
  // Shared with App's gate (same cache key): the active org + every org this
  // user belongs to. Drives the switcher below.
  const orgs = useQuery({
    queryKey: ['my-orgs'],
    queryFn: () => api.get('/api/auth/orgs'),
    retry: false,
  })
  const openCount = alerts.data?.length || 0

  async function logout() {
    await api.post('/api/auth/logout')
    queryClient.setQueryData(['me'], null)
    queryClient.invalidateQueries()
  }

  async function switchOrg(e) {
    const id = Number(e.target.value)
    try {
      await api.post('/api/auth/select-org', { org_id: id })
      // Every query is org-scoped — drop the cache so nothing from the old
      // workspace lingers, then let React Query refetch against the new token.
      await queryClient.invalidateQueries()
    } catch (err) {
      toast(err.message, 'error')
    }
  }

  const currentOrg = orgs.data?.current
  const showSwitcher = orgs.data && orgs.data.orgs.length > 1

  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 flex w-60 flex-col border-r border-slate-200 bg-slate-900 text-slate-300">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <Archive className="text-amber-400" size={26} />
          <div>
            <div className="text-lg font-bold tracking-tight text-white">Nestbox</div>
            <div className="text-[11px] text-slate-400">Records, tracked & boxed</div>
          </div>
        </div>

        {showSwitcher && (
          <div className="px-3 pb-2">
            <select value={currentOrg?.id ?? ''} onChange={switchOrg} aria-label="Switch organization"
                    className="w-full cursor-pointer rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:border-amber-400 focus:outline-none">
              {orgs.data.orgs.map((o) => (
                <option key={o.id} value={o.id}>{o.name}</option>
              ))}
            </select>
            {currentOrg && (
              <div className="mt-1 px-1 text-[11px] capitalize text-slate-500">{currentOrg.kind} workspace</div>
            )}
          </div>
        )}

        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-2">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? 'bg-amber-500/15 text-amber-300' : 'hover:bg-slate-800 hover:text-white'
                }`
              }
            >
              <Icon size={17} />
              {label}
              {label === 'Alerts' && openCount > 0 && (
                <span className="ml-auto rounded-full bg-rose-500 px-1.5 py-0.5 text-[10px] font-bold text-white">
                  {openCount > 99 ? '99+' : openCount}
                </span>
              )}
            </NavLink>
          ))}
          {user.role === 'admin' && (
            <NavLink
              to="/onboarding"
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? 'bg-amber-500/15 text-amber-300' : 'hover:bg-slate-800 hover:text-white'
                }`
              }
            >
              <Wand2 size={17} />
              Auto-categorize
            </NavLink>
          )}
          {user.role === 'admin' && (
            <NavLink
              to="/admin/orgs"
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? 'bg-amber-500/15 text-amber-300' : 'hover:bg-slate-800 hover:text-white'
                }`
              }
            >
              <Building2 size={17} />
              Organizations
            </NavLink>
          )}
          {user.role === 'admin' && (
            <NavLink
              to="/admin"
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? 'bg-amber-500/15 text-amber-300' : 'hover:bg-slate-800 hover:text-white'
                }`
              }
            >
              <Settings size={17} />
              Admin
            </NavLink>
          )}
          {user.role === 'admin' && (
            <NavLink
              to="/admin/database"
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? 'bg-amber-500/15 text-amber-300' : 'hover:bg-slate-800 hover:text-white'
                }`
              }
            >
              <DatabaseIcon size={17} />
              Database
            </NavLink>
          )}
        </nav>
        <div className="border-t border-slate-800 px-5 py-4">
          <div className="mb-2 text-sm">
            <div className="font-medium text-white">{user.full_name || user.username}</div>
            <div className="text-xs text-slate-400 capitalize">{user.role}</div>
          </div>
          <button onClick={logout}
                  className="flex items-center gap-2 text-xs text-slate-400 hover:text-white">
            <LogOut size={13} /> Sign out
          </button>
        </div>
      </aside>
      <main className="ml-60 flex-1 p-8">{children}</main>
    </div>
  )
}
