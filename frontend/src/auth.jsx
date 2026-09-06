import React, { createContext, useContext, useEffect, useState } from 'react'
import { loadAuth, saveAuth, clearAuth, api } from './api'

const AuthCtx = createContext(null)

export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(loadAuth())
  const [me, setMe] = useState(null)

  useEffect(() => {
    if (auth?.access_token) {
      api('/auth/me')
        .then(setMe)
        .catch(() => setMe({ username: auth.username || '?', role: auth.role || '?' }))
    }
  }, [auth?.access_token])

  const value = {
    auth,
    me,
    login: async (username, password) => {
      const pair = await api('/auth/login', { method: 'POST', body: { username, password } })
      const next = { ...pair, username }
      saveAuth(next)
      setAuth(next)
      return next
    },
    logout: () => {
      clearAuth()
      setAuth(null)
      setMe(null)
    },
    role: me?.role || auth?.role || null,
  }
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>
}

export const useAuth = () => useContext(AuthCtx)
