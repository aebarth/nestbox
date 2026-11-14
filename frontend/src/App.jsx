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
import Inbox from './pages/Inbox.jsx'
import Login from './pages/Login.jsx'
import Onboarding from './pages/Onboarding.jsx'
import Organizations from './pages/Organizations.jsx'
import OrgPicker from './pages/OrgPicker.jsx'
import RecordLink from './pages/RecordLink.jsx'
import Reports from './pages/Reports.jsx'
import Wizard from './pages/Wizard.jsx'
import { api } from './services/api.js'

export default function App() {
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: ['auth-status'], queryFn: () => api.get('/api/auth/status') })
  const needsSetup = !!status.data?.needs_setup

  // /orgs is the session discriminator for multi-org (#10): it answers "which
  // org am I in (if any)?" and works with both a full token and the short-lived
  // pending token issued after a multi-org login. A 401 means no session at all.
  const orgs = useQuery({
    queryKey: ['my-orgs'],
    queryFn: () => api.get('/api/auth/orgs'),
    enabled: status.data?.needs_setup === false,
    retry: false,
  })
  // Only meaningful once an org has been selected.
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get('/api/auth/me'),
    enabled: !!orgs.data?.current,
    retry: false,
  })

  useEffect(() => {
    const onUnauthorized = () => queryClient.invalidateQueries()
    window.addEventListener('nestbox:unauthorized', onUnauthorized)
    return () => window.removeEventListener('nestbox:unauthorized', onUnauthorized)
  }, [queryClient])

  if (status.isLoading) {
    return <Spinner label="Starting Nestbox…" />
  }

  if (needsSetup) {
    return (
      <ToastProvider>
        <Login needsSetup />
      </ToastProvider>
    )
  }

  const current = orgs.data?.current
  if (orgs.isLoading || (current && me.isLoading)) {
    return <Spinner label="Starting Nestbox…" />
  }

  // No session at all (401) → username/password sign-in.
  if (!current && !orgs.data) {
    return (
      <ToastProvider>
        <Login />
      </ToastProvider>
    )
  }

  // Session exists but no org chosen yet → workspace picker.
  if (!current) {
    return (
      <ToastProvider>
        <OrgPicker orgs={orgs.data?.orgs || []} />
      </ToastProvider>
    )
  }

  const user = me.data
  if (!user) {
    return <Spinner label="Starting Nestbox…" />
  }

  return (
    <ToastProvider>
      <Layout user={user}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/files" element={<Files user={user} />} />
          <Route path="/inbox" element={<Inbox user={user} />} />
          <Route path="/wizard" element={<Wizard />} />
          <Route path="/boxes" element={<Boxes />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/onboarding" element={user.role === 'admin' ? <Onboarding /> : <Navigate to="/" />} />
          <Route path="/admin" element={user.role === 'admin' ? <Admin /> : <Navigate to="/" />} />
          <Route path="/admin/orgs" element={user.role === 'admin' ? <Organizations /> : <Navigate to="/" />} />
          <Route path="/admin/database" element={user.role === 'admin' ? <Database /> : <Navigate to="/" />} />
          <Route path="/r/:uuid" element={<RecordLink />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Layout>
    </ToastProvider>
  )
}
