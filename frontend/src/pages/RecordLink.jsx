import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { useEffect } from 'react'
import { Spinner } from '../components/ui.jsx'
import { api } from '../services/api.js'

/** QR-code landing page: /r/:uuid -> open the record in Files. */
export default function RecordLink() {
  const { uuid } = useParams()
  const navigate = useNavigate()
  const record = useQuery({
    queryKey: ['record-uuid', uuid],
    queryFn: () => api.get(`/api/records/by-uuid/${uuid}`),
  })

  useEffect(() => {
    if (record.data) navigate(`/files?record=${record.data.id}`, { replace: true })
  }, [record.data, navigate])

  if (record.isError) {
    return <div className="py-20 text-center text-slate-500">Record not found.</div>
  }
  return <Spinner label="Opening record…" />
}
