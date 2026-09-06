import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'

// Admin governance console — the highest-authority surface.
// Users: create / reset password / change role / (de)activate, all audited.
// Permissions: the live role→permission matrix; toggling a grant that other
// roles already hold surfaces the "same as so-and-so role" hint.

const ROLES = ['admin', 'operator', 'checker', 'certifier', 'auditor']

export default function AdminPage() {
  const { role, me } = useAuth()
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
        <p className="muted">Only the admin role can open this page.</p>
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
      <h1>Admin</h1>
      <p className="muted">Every action here is written to the hash-chained audit trail.</p>
      {msg && <div className="ok-box">{msg}</div>}
      {err && <div className="error-box">{err}</div>}
      <div className="row" style={{ margin: '10px 0' }}>
        <button className={`btn btn-sm${tab === 'users' ? ' btn-primary' : ''}`} onClick={() => setTab('users')}>Users</button>
        <button className={`btn btn-sm${tab === 'perm' ? ' btn-primary' : ''}`} onClick={() => setTab('perm')}>Permissions</button>
      </div>
      {tab === 'users' ? <Users users={users} me={me} act={act} /> : <Permissions perm={perm} act={act} />}
    </div>
  )
}

function Users({ users, me, act }) {
  const [nu, setNu] = useState({ username: '', password: '', role: 'checker' })
  const [pw, setPw] = useState({})

  if (!users) return <p className="muted">Loading…</p>
  return (
    <>
      <div className="card">
        <h2>Create user</h2>
        <div className="row" style={{ alignItems: 'flex-end', flexWrap: 'wrap', gap: 8 }}>
          <div className="field">
            <label>Username</label>
            <input value={nu.username} onChange={(e) => setNu({ ...nu, username: e.target.value })} />
          </div>
          <div className="field">
            <label>Password (min 8 chars)</label>
            <input type="password" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} />
          </div>
          <div className="field">
            <label>Role</label>
            <select value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}>
              {ROLES.map((r) => <option key={r}>{r}</option>)}
            </select>
          </div>
          <button className="btn btn-primary" disabled={nu.username.length < 3 || nu.password.length < 8}
            onClick={() => act(
              () => api('/admin/users', { method: 'POST', body: nu }),
              `User '${nu.username}' created as ${nu.role}.`
            ).then(() => setNu({ username: '', password: '', role: 'checker' }))}>
            Create
          </button>
        </div>
      </div>
      <div className="card">
        <h2>All users ({users.users.length})</h2>
        <table>
          <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Decisions</th><th>Actions</th></tr></thead>
          <tbody>
            {users.users.map((u) => (
              <tr key={u.id}>
                <td><b>{u.username}</b>{u.username === me?.username && <span className="muted"> (you)</span>}</td>
                <td>
                  <select value={u.role} disabled={u.username === me?.username}
                    onChange={(e) => act(
                      () => api(`/admin/users/${u.username}/role`, { method: 'POST', body: { role: e.target.value } }),
                      `${u.username} is now ${e.target.value}.`
                    )}>
                    {ROLES.map((r) => <option key={r}>{r}</option>)}
                  </select>
                </td>
                <td>
                  <span className={`chip ${u.is_active ? '' : 'sev-warn'}`}>{u.is_active ? 'active' : 'inactive'}</span>
                </td>
                <td className="muted">{u.decisions}</td>
                <td>
                  <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                    <input type="password" placeholder="new password" style={{ width: 140 }}
                      value={pw[u.username] || ''}
                      onChange={(e) => setPw({ ...pw, [u.username]: e.target.value })} />
                    <button className="btn btn-sm" disabled={(pw[u.username] || '').length < 8}
                      onClick={() => act(
                        () => api(`/admin/users/${u.username}/password`, { method: 'POST', body: { new_password: pw[u.username] } }),
                        `Password reset for ${u.username}.`
                      ).then(() => setPw({ ...pw, [u.username]: '' }))}>
                      Reset
                    </button>
                    {u.username !== me?.username && (
                      <button className="btn btn-sm"
                        onClick={() => act(
                          () => api(`/admin/users/${u.username}/active`, { method: 'POST', body: { is_active: !u.is_active } }),
                          `${u.username} ${u.is_active ? 'deactivated' : 'reactivated'}.`
                        )}>
                        {u.is_active ? 'Deactivate' : 'Activate'}
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
  const [note, setNote] = useState(null)

  if (!perm) return <p className="muted">Loading…</p>

  const toggle = async (role, permission, allowed) => {
    setNote(null)
    try {
      const res = await api(`/admin/permissions/${role}/${permission}`, { method: 'POST', body: { allowed } })
      if (res.note) setNote(res.note)
    } catch (e) { throw e }
  }

  return (
    <>
      {note && <div className="card"><p style={{ margin: 0 }}>{note}</p></div>}
      <div className="card" style={{ overflowX: 'auto' }}>
        <h2>Role → permission matrix (live)</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          Toggles apply immediately — no redeploy. Granting a permission another role already
          holds will show you which. Admin always retains everything (last-resort authority).
        </p>
        <table>
          <thead>
            <tr>
              <th style={{ textAlign: 'left' }}>Permission</th>
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
                        <span className="chip" title="Admin always retains all permissions">always</span>
                      ) : (
                        <button
                          className={`chip ${on ? 'chip-ok' : 'chip-off'}`}
                          title={on ? 'click to revoke' : 'click to grant'}
                          onClick={() => act(() => toggle(r, c.permission, !on),
                            `${c.permission} ${on ? 'revoked from' : 'granted to'} ${r}.`)}>
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
        <p className="muted" style={{ fontSize: 12 }}>* = default grant for this role. Changes take effect within ~10 seconds.</p>
      </div>
    </>
  )
}
