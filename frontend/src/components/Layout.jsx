import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  Bell,
  FileText,
  LayoutDashboard,
  LogOut,
  Package,
  ScanLine,
  Settings,
  FileBarChart,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { api } from '../services/api.js'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/files', label: 'Files', icon: FileText },
  { to: '/wizard', label: 'Scan to Shelf', icon: ScanLine },
  { to: '/boxes', label: 'Boxes', icon: Package },
  { to: '/alerts', label: 'Alerts', icon: Bell },
  { to: '/reports', label: 'Reports', icon: FileBarChart },
]

export default function Layout({ user, children }) {
  const queryClient = useQueryClient()
  const alerts = useQuery({
    queryKey: ['alerts', 'open-count'],
    queryFn: () => api.get('/api/alerts?status=unresolved&limit=500'),
    refetchInterval: 60_000,
  })
  const openCount = alerts.data?.length || 0

  async function logout() {
    await api.post('/api/auth/logout')
    queryClient.setQueryData(['me'], null)
  }

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
        <nav className="flex-1 space-y-1 px-3 py-2">
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
