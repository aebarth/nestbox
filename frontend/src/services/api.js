async function request(path, options = {}) {
  const opts = { headers: {}, ...options }
  if (opts.body !== undefined && typeof opts.body !== 'string') {
    opts.body = JSON.stringify(opts.body)
    opts.headers['Content-Type'] = 'application/json'
  }
  const resp = await fetch(path, opts)
  if (resp.status === 401 && !path.startsWith('/api/auth')) {
    window.dispatchEvent(new Event('nestbox:unauthorized'))
  }
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const data = await resp.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch { /* not json */ }
    const err = new Error(detail)
    err.status = resp.status
    throw err
  }
  if (resp.headers.get('content-type')?.includes('application/json')) return resp.json()
  return resp
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: 'POST', body }),
  patch: (path, body) => request(path, { method: 'PATCH', body }),
  put: (path, body) => request(path, { method: 'PUT', body }),
  del: (path) => request(path, { method: 'DELETE' }),
}

export function qs(params) {
  const p = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '' && v !== false) p.set(k, v)
  })
  const s = p.toString()
  return s ? `?${s}` : ''
}
