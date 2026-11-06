import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout.jsx'
import { Spinner, ToastProvider } from './components/ui.jsx'
import Admin from './pages/Admin.jsx'
import Alerts from './pages/Alerts.jsx'
import Boxes from './pages/Boxes.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Database from './pages/Database.jsx'
import Files from './pages/Files.jsx'
import Login from './pages/Login.jsx'
import Onboarding from './pages/Onboarding.jsx'
import RecordLink from './pages/RecordLink.jsx'
import Reports from './pages/Reports.jsx'
import Wizard from './pages/Wizard.jsx'
import { api } from './services/api.js'

export default function App() {
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: ['auth-status'], queryFn: () => api.get('/api/auth/status') })
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get('/api/auth/me'),
    enabled: status.data?.needs_setup === false,
    retry: false,
  })

  useEffect(() => {
    const onUnauthorized = () => queryClient.setQueryData(['me'], null)
    window.addEventListener('nestbox:unauthorized', onUnauthorized)
    return () => window.removeEventListener('nestbox:unauthorized', onUnauthorized)
  }, [queryClient])

  if (status.isLoading || (status.data?.needs_setup === false && me.isLoading)) {
    return <Spinner label="Starting Nestbox…" />
  }

  const user = me.data
  if (status.data?.needs_setup || !user) {
    return (
      <ToastProvider>
        <Login needsSetup={!!status.data?.needs_setup} />
      </ToastProvider>
    )
  }

  return (
    <ToastProvider>
      <Layout user={user}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/files" element={<Files user={user} />} />
          <Route path="/wizard" element={<Wizard />} />
          <Route path="/boxes" element={<Boxes />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/onboarding" element={user.role === 'admin' ? <Onboarding /> : <Navigate to="/" />} />
          <Route path="/admin" element={user.role === 'admin' ? <Admin /> : <Navigate to="/" />} />
          <Route path="/admin/database" element={user.role === 'admin' ? <Database /> : <Navigate to="/" />} />
          <Route path="/r/:uuid" element={<RecordLink />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Layout>
    </ToastProvider>
  )
}
