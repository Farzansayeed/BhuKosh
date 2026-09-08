import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { LANGS, useI18n } from '../i18n'

export default function LoginPage() {
  const { login } = useAuth()
  const { t, lang, setLang } = useI18n()
  const nav = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
      nav('/records')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
      <form onSubmit={submit} className="card" style={{ width: 360 }}>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="brand" style={{ padding: 0 }}><img src="/favicon.svg" alt="" width="24" height="24" style={{ verticalAlign: '-5px', marginRight: 7, borderRadius: 6 }} />Bhu<span>Kosh</span></div>
          <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label={t('language')} style={{ fontSize: 12 }}>
            {LANGS.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
          </select>
        </div>
        <p className="sub">{t('login_tagline')}</p>
        <div className="field">
          <label>{t('username')}</label>
          <input autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div className="field">
          <label>{t('password')}</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {error && <div className="error-box">{error}</div>}
        <button className="btn btn-primary" style={{ width: '100%' }} disabled={busy || !username || !password}>
          {busy ? t('signing_in') : t('sign_in')}
        </button>
      </form>
    </div>
  )
}
