const TOKEN_KEY = 'bhukosh.auth'

export function loadAuth() {
  try {
    return JSON.parse(localStorage.getItem(TOKEN_KEY))
  } catch {
    return null
  }
}

export function saveAuth(auth) {
  localStorage.setItem(TOKEN_KEY, JSON.stringify(auth))
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
}

// Thin fetch wrapper: JSON in/out, bearer token attached, RFC-7807 problem
// detail surfaced as a thrown Error with .status/.detail/.problem so pages
// can render honest error messages.
export async function api(path, { method = 'GET', body, formData } = {}) {
  const auth = loadAuth()
  const headers = {}
  if (auth?.access_token) headers['Authorization'] = `Bearer ${auth.access_token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  const resp = await fetch(`/api${path}`, {
    method,
    headers,
    body: formData ? formData : body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (resp.status === 401 && auth) {
    // try one silent refresh, then replay the request
    const refreshed = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: auth.refresh_token }),
    })
    if (refreshed.ok) {
      const pair = await refreshed.json()
      const next = { ...auth, ...pair }
      saveAuth(next)
      headers['Authorization'] = `Bearer ${pair.access_token}`
      const retry = await fetch(`/api${path}`, {
        method,
        headers,
        body: formData ? formData : body !== undefined ? JSON.stringify(body) : undefined,
      })
      return finish(retry)
    }
    clearAuth()
    window.location.href = '/login'
    throw new Error('Session expired. Please sign in again.')
  }
  return finish(resp)
}

// Authenticated binary download (crop images, exports): returns a Blob.
export async function apiBlob(path) {
  const auth = loadAuth()
  const resp = await fetch(`/api${path}`, {
    headers: auth?.access_token ? { Authorization: `Bearer ${auth.access_token}` } : {},
  })
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`
    try { const j = await resp.json(); detail = j.detail || detail } catch { /* not json */ }
    throw new Error(detail)
  }
  return resp.blob()
}

async function finish(resp) {
  if (resp.ok) {
    const ct = resp.headers.get('content-type') || ''
    return ct.includes('json') ? resp.json() : resp.text()
  }
  let detail = `${resp.status} ${resp.statusText}`
  try {
    const ct = resp.headers.get('content-type') || ''
    if (ct.includes('json')) {
      const problem = await resp.json()
      detail = problem.detail || problem.title || detail
      const err = new Error(detail)
      err.status = resp.status
      err.problem = problem
      throw err
    }
  } catch (e) {
    if (e.status) throw e
  }
  const err = new Error(detail)
  err.status = resp.status
  throw err
}
