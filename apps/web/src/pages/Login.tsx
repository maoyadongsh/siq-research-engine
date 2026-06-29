import React, { useState } from 'react'
import { ArrowRight, Loader2, LogIn, PlayCircle } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

interface LoginFormData {
  username: string
  password: string
}

export function LoginPage() {
  const [formData, setFormData] = useState<LoginFormData>({
    username: '',
    password: '',
  })
  const [manualEntryEnabled, setManualEntryEnabled] = useState(false)
  const [error, setError] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const { login } = useAuth()
  const enableManualEntry = () => setManualEntryEnabled(true)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      await login(formData.username, formData.password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-mobile-poster" aria-hidden="true">
          <PlayCircle className="h-8 w-8 text-white/90" />
        </div>
        <div className="auth-brand">
          <div className="auth-logo" aria-hidden="true">SIQ</div>
          <div>
            <p className="auth-kicker">Research Engine</p>
            <h1 id="login-title" className="auth-title">登录 SIQ</h1>
            <p className="auth-description">进入财报搜索、解析、分析与审计工作台。</p>
          </div>
        </div>

        <form className="auth-form" onSubmit={handleSubmit} autoComplete="off" data-form-type="other">
          {error && (
            <div className="auth-alert" role="alert">
              {error}
            </div>
          )}

          <label className="auth-field" htmlFor="username">
            <span>用户名</span>
            <input
              id="username"
              name="siq_manual_user"
              type="text"
              required
              autoComplete="new-password"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              readOnly={!manualEntryEnabled}
              data-lpignore="true"
              data-1p-ignore="true"
              data-form-type="other"
              onFocus={enableManualEntry}
              onPointerDown={enableManualEntry}
              value={formData.username}
              onChange={(e) => setFormData({ ...formData, username: e.target.value })}
              placeholder="例如 maoyd"
            />
          </label>

          <label className="auth-field" htmlFor="password">
            <span>密码</span>
            <input
              id="password"
              name="siq_manual_secret"
              type="password"
              required
              autoComplete="new-password"
              readOnly={!manualEntryEnabled}
              data-lpignore="true"
              data-1p-ignore="true"
              data-form-type="other"
              onFocus={enableManualEntry}
              onPointerDown={enableManualEntry}
              value={formData.password}
              onChange={(e) => setFormData({ ...formData, password: e.target.value })}
              placeholder="输入账户密码"
            />
          </label>

          <button type="submit" disabled={loading} className="auth-primary">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
            {loading ? '登录中…' : '登录'}
          </button>

          <button type="button" onClick={() => navigate('/register')} className="auth-secondary">
            创建新账户
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </button>
        </form>

        <div className="auth-video">
          <div className="auth-video-head">
            <PlayCircle className="h-4 w-4 text-primary" aria-hidden="true" />
            <span>快速了解 SIQ 工作流</span>
          </div>
          <video
            controls
            playsInline
            preload="metadata"
            className="w-full"
            poster="/videos/siq-login-20260608-poster.jpg?v=20260608"
          >
            <source src="/videos/siq-login-mobile-20260608.mp4?v=20260608" type="video/mp4" />
            您的浏览器不支持视频播放
          </video>
        </div>

        <p className="auth-version">SIQ v2.0 · 企业级财务分析系统</p>
      </section>
    </main>
  )
}

export default LoginPage
