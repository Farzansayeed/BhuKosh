import React from 'react'
import { Navigate, NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from './auth'
import { LANGS, useI18n } from './i18n'

export function RequireAuth({ children }) {
  const { auth } = useAuth()
  if (!auth) return <Navigate to="/login" replace />
  return children
}

export function Layout() {
  const { me, role, logout } = useAuth()
  const { t, lang, setLang } = useI18n()
  const nav = useNavigate()
  const isAuditor = role === 'admin' || role === 'auditor'

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand"><img src="/favicon.svg" alt="" width="22" height="22" style={{ verticalAlign: '-4px', marginRight: 6, borderRadius: 5 }} />Bhu<span>Kosh</span></div>
        <NavLink to="/" end className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>{t('nav_dashboard')}</NavLink>
        <NavLink to="/records" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>{t('nav_records')}</NavLink>
        <NavLink to="/upload" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>{t('nav_upload')}</NavLink>
        {isAuditor && (
          <NavLink to="/audit" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>{t('nav_audit')}</NavLink>
        )}
        {role === 'admin' && (
          <NavLink to="/admin" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>{t('nav_admin')}</NavLink>
        )}
        <div className="spacer" />
        <div className="user-box">
          <div className="field lang-pick" style={{ marginBottom: 8 }}>
            <select
              value={lang}
              onChange={(e) => setLang(e.target.value)}
              aria-label={t('language')}
              title={t('language')}
            >
              {LANGS.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
            </select>
          </div>
          {me ? <>{t('signed_in_as')}<br /><b>{me.username}</b><br /><span className="role-chip">{role}</span></> : '…'}
          <div className="mt">
            <button
              className="btn btn-sm"
              onClick={() => { logout(); nav('/login') }}
            >{t('sign_out')}</button>
          </div>
        </div>
      </aside>
      <main className="main"><Outlet /></main>
    </div>
  )
}
