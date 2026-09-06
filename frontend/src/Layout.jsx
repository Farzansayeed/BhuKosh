import React from 'react'
import { Navigate, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from './auth'

export function RequireAuth({ children }) {
  const { auth } = useAuth()
  if (!auth) return <Navigate to="/login" replace />
  return children
}

export function Layout() {
  const { me, role, logout } = useAuth()
  const nav = useNavigate()
  const isAuditor = role === 'admin' || role === 'auditor'

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">Bhu<span>Kosh</span></div>
        <NavLink to="/records" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>Records</NavLink>
        <NavLink to="/upload" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>Upload & Extract</NavLink>
        {isAuditor && (
          <NavLink to="/audit" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>Audit Chain</NavLink>
        )}
        <div className="spacer" />
        <div className="user-box">
          {me ? <>signed in as<br /><b>{me.username}</b><br /><span className="role-chip">{role}</span></> : '…'}
          <div className="mt">
            <button
              className="btn btn-sm"
              onClick={() => { logout(); nav('/login') }}
            >Sign out</button>
          </div>
        </div>
      </aside>
      <main className="main"><Outlet /></main>
    </div>
  )
}
