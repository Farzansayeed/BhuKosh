import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import { useI18n } from '../i18n'

// Admin governance console — the highest-authority surface.
// Users: create / reset password / change role / (de)activate, all audited.
// Permissions: the live role→permission matrix; toggling a grant that other
// roles already hold surfaces the "same as so-and-so role" hint.

const ROLES = ['admin', 'operator', 'checker', 'certifier', 'auditor']

export default function AdminPage() {
  const { role, me } = useAuth()
  const { t } = useI18n()
  const [tab, setTab] = useState('users')
  const [users, setUsers] = useState(null)
  const [perm, setPerm] = useState(null)
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)

  const isAdmin = role === 'admin'

  const loadUsers = () => api('/admin/users').then(setUsers).catch((e) => setErr(e.message))
  const loadPerm = () => api('/admin/permissions').then(setPerm).catch((e) => setErr(e.message))

  useEffect(() => {
    if (!isAdmin) return
    setErr(null)
    if (tab === 'users') loadUsers()
    else loadPerm()
  }, [tab, isAdmin])

  if (!isAdmin) {
    return (
      <div className="card">
        <h1>Admin</h1>
        <p className="muted">{t('admin_only')}</p>
      </div>
    )
  }

  const act = async (fn, ok) => {
    setErr(null); setMsg(null)
    try { await fn(); setMsg(ok); tab === 'users' ? loadUsers() : loadPerm() }
    catch (e) { setErr(e.message) }
  }

  return (
    <div>
      <h1>{t('nav_admin')}</h1>
      <p className="muted">{t('admin_audited')}</p>
      {msg && <div className="ok-box">{msg}</div>}
      {err && <div className="error-box">{err}</div>}
      <div className="row" style={{ margin: '10px 0' }}>
        <button className={`btn btn-sm${tab === 'users' ? ' btn-primary' : ''}`} onClick={() => setTab('users')}>{t('users_tab')}</button>
        <button className={`btn btn-sm${tab === 'perm' ? ' btn-primary' : ''}`} onClick={() => setTab('perm')}>{t('perms_tab')}</button>
      </div>
      {tab === 'users' ? <Users users={users} me={me} act={act} /> : <Permissions perm={perm} act={act} />}
    </div>
  )
}

function Users({ users, me, act }) {
  const { t } = useI18n()
  const [nu, setNu] = useState({ username: '', password: '', role: 'checker' })
  const [pw, setPw] = useState({})

  if (!users) return <p className="muted">{t('loading')}</p>
  return (
    <>
      <div className="card">
        <h2>{t('create_user')}</h2>
        <div className="row" style={{ alignItems: 'flex-end', flexWrap: 'wrap', gap: 8 }}>
          <div className="field">
            <label>{t('username')}</label>
            <input value={nu.username} onChange={(e) => setNu({ ...nu, username: e.target.value })} />
          </div>
          <div className="field">
            <label>{t('password_min')}</label>
            <input type="password" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} />
          </div>
          <div className="field">
            <label>{t('col_role')}</label>
            <select value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}>
              {ROLES.map((r) => <option key={r}>{r}</option>)}
            </select>
          </div>
          <button className="btn btn-primary" disabled={nu.username.length < 3 || nu.password.length < 8}
            onClick={() => act(
              () => api('/admin/users', { method: 'POST', body: nu }),
              t('user_created', { name: nu.username, role: nu.role })
            ).then(() => setNu({ username: '', password: '', role: 'checker' }))}>
            {t('create_user')}
          </button>
        </div>
      </div>
      <div className="card">
        <h2>{t('all_users', { n: users.users.length })}</h2>
        <table>
          <thead><tr><th>{t('col_user')}</th><th>{t('col_role')}</th><th>{t('col_status')}</th><th>{t('col_decisions')}</th><th>{t('col_actions')}</th></tr></thead>
          <tbody>
            {users.users.map((u) => (
              <tr key={u.id}>
                <td><b>{u.username}</b>{u.username === me?.username && <span className="muted"> {t('you')}</span>}</td>
                <td>
                  <select value={u.role} disabled={u.username === me?.username}
                    onChange={(e) => act(
                      () => api(`/admin/users/${u.username}/role`, { method: 'POST', body: { role: e.target.value } }),
                      t('user_role_now', { name: u.username, role: e.target.value })
                    )}>
                    {ROLES.map((r) => <option key={r}>{r}</option>)}
                  </select>
                </td>
                <td>
                  <span className={`chip ${u.is_active ? '' : 'sev-warn'}`}>{u.is_active ? t('active') : t('inactive')}</span>
                </td>
                <td className="muted">{u.decisions}</td>
                <td>
                  <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                    <input type="password" placeholder={t('new_password')} style={{ width: 140 }}
                      value={pw[u.username] || ''}
                      onChange={(e) => setPw({ ...pw, [u.username]: e.target.value })} />
                    <button className="btn btn-sm" disabled={(pw[u.username] || '').length < 8}
                      onClick={() => act(
                        () => api(`/admin/users/${u.username}/password`, { method: 'POST', body: { new_password: pw[u.username] } }),
                        t('password_reset', { name: u.username })
                      ).then(() => setPw({ ...pw, [u.username]: '' }))}>
                      {t('reset')}
                    </button>
                    {u.username !== me?.username && (
                      <button className="btn btn-sm"
                        onClick={() => act(
                          () => api(`/admin/users/${u.username}/active`, { method: 'POST', body: { is_active: !u.is_active } }),
                          u.is_active ? t('user_deactivated', { name: u.username }) : t('user_reactivated', { name: u.username })
                        )}>
                        {u.is_active ? t('deactivate') : t('activate')}
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Permissions({ perm, act }) {
  const { t } = useI18n()
  const [note, setNote] = useState(null)
  const [confirmReset, setConfirmReset] = useState(false)

  if (!perm) return <p className="muted">{t('loading')}</p>

  const toggle = async (role, permission, allowed) => {
    setNote(null)
    try {
      const res = await api(`/admin/permissions/${role}/${permission}`, { method: 'POST', body: { allowed } })
      if (res.note) setNote(res.note)
    } catch (e) { throw e }
  }

  const resetAll = async () => {
    setConfirmReset(false)
    await act(
      () => api('/admin/permissions/reset', { method: 'POST', body: {} }),
      t('perm_reset_msg')
    )
  }

  return (
    <>
      {note && <div className="card"><p style={{ margin: 0 }}>{note}</p></div>}
      <div className="card" style={{ overflowX: 'auto' }}>
        <div className="row">
          <h2 style={{ margin: 0 }}>{t('perm_matrix')}</h2>
          {confirmReset ? (
            <>
              <span className="muted" style={{ fontSize: 12 }}>{t('restore_defaults_q')}</span>
              <button className="btn btn-sm btn-danger" onClick={resetAll}>{t('yes_reset')}</button>
              <button className="btn btn-sm" onClick={() => setConfirmReset(false)}>{t('cancel')}</button>
            </>
          ) : (
            <button className="btn btn-sm right" title={t('reset_tip')}
              onClick={() => setConfirmReset(true)}>
              {t('reset_defaults')}
            </button>
          )}
        </div>
        <p className="muted" style={{ marginTop: 8 }}>{t('perm_sub')}</p>
        <table className="matrix-table">
          <thead>
            <tr>
              <th style={{ textAlign: 'left' }}>{t('permission')}</th>
              {perm.roles.map((r) => <th key={r}>{r}</th>)}
            </tr>
          </thead>
          <tbody>
            {perm.catalog.map((c) => (
              <tr key={c.permission}>
                <td style={{ textAlign: 'left' }}>
                  <span className="mono">{c.permission}</span><br />
                  <span className="muted" style={{ fontSize: 12 }}>{c.description}</span>
                </td>
                {perm.roles.map((r) => {
                  const on = perm.matrix[r]?.includes(c.permission)
                  const isDefault = perm.defaults[r]?.includes(c.permission)
                  const isAdminCol = r === 'admin'
                  return (
                    <td key={r} style={{ textAlign: 'center' }}>
                      {isAdminCol ? (
                        <span className="chip" title={t('admin_always_tip')}>{t('always')}</span>
                      ) : (
                        <button
                          className={`chip ${on ? 'chip-ok' : 'chip-off'}`}
                          title={on ? t('click_revoke') : t('click_grant')}
                          onClick={() => act(() => toggle(r, c.permission, !on),
                            t('perm_granted_msg', { perm: c.permission, role: r, verb: on ? t('revoked_from') : t('granted_to') }))}>
                          {on ? '✓' : '—'}{isDefault ? '*' : ''}
                        </button>
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: 12 }}>{t('perm_default_note')}</p>
      </div>
    </>
  )
}
