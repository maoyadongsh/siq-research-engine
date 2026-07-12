import React, { useState } from 'react'
import { ArrowRight, BadgeCheck, FileSearch, Loader2, LogIn, ShieldCheck, Sparkles } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

const loginHighlights = [
  {
    icon: FileSearch,
    label: '全球市场上市公司财报检索',
  },
  {
    icon: Sparkles,
    label: '高精度财报解析和指标抽取',
  },
  {
    icon: ShieldCheck,
    label: '全流程证据链可回溯可审计',
  },
  {
    icon: BadgeCheck,
    label: '全球一二级市场全方位洞察',
  },
]

interface LoginFormData {
  username: string
  password: string
}

const demoLoginDefaultsEnabled = import.meta.env.VITE_SIQ_DEMO_LOGIN_DEFAULTS === '1'

const defaultLoginFormData: LoginFormData = {
  username: import.meta.env.VITE_SIQ_LOGIN_DEFAULT_USERNAME || (demoLoginDefaultsEnabled ? 'admin' : ''),
  password: import.meta.env.VITE_SIQ_LOGIN_DEFAULT_PASSWORD || (demoLoginDefaultsEnabled ? 'Admin@123456' : ''),
}

export function LoginPage() {
  const [formData, setFormData] = useState<LoginFormData>(defaultLoginFormData)
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
      const returnTo = sessionStorage.getItem('siq_auth_return_to')
      sessionStorage.removeItem('siq_auth_return_to')
      const safeReturnTo = returnTo && returnTo.startsWith('/') && !returnTo.startsWith('//') && !/^\/(?:login|logout)(?:\/|$)/.test(returnTo)
        ? returnTo
        : '/'
      navigate(safeReturnTo, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="auth-shell auth-shell-login">
      <section className="auth-card auth-card-login" aria-labelledby="login-title">
        <div className="auth-copy" aria-label="SIQ Research Engine 主权智感投研决策引擎">
          <div className="auth-copy-main">
            <div className="auth-copy-topline">
              <div className="auth-copy-mark" aria-hidden="true">
                <span>SIQ</span>
              </div>
              <span>Sovereign Intelligence Quotient Research Engine</span>
            </div>
            <h2>主权智感投研决策引擎</h2>
            <p>
              全球一二级市场研究一站解决
              <br />
              基于全链路可审计的公司、行业研究平台
            </p>

            <div className="auth-market-strip" aria-hidden="true">
              <span>A股市场</span>
              <span>香港市场</span>
              <span>美国市场</span>
              <span>欧洲市场</span>
              <span>韩国市场</span>
              <span>日本市场</span>
            </div>
          </div>

          <div className="auth-copy-list" aria-hidden="true">
            {loginHighlights.map((item) => {
              const Icon = item.icon
              return (
                <div key={item.label}>
                  <Icon className="h-4 w-4" />
                  <span>{item.label}</span>
                </div>
              )
            })}
          </div>

          <div className="auth-copy-foot" aria-hidden="true">
            <span>Financial Research OS</span>
            <span>Audit-ready Evidence</span>
          </div>
        </div>

        <div className="auth-form-panel">
          <h1 id="login-title" className="sr-only">登录 SIQ Research Engine</h1>

          <div className="auth-form-kicker" aria-hidden="true">SIQ Research Engine</div>
          <p className="auth-form-welcome">欢迎来到SIQ</p>

          <form className="auth-form auth-login-form" onSubmit={handleSubmit} autoComplete="on">
            {error && (
              <div className="auth-alert" role="alert">
                {error}
              </div>
            )}

            <label className="auth-field" htmlFor="username">
              <span>用户名</span>
              <input
                id="username"
                name="username"
                type="text"
                required
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                readOnly={!manualEntryEnabled}
                onFocus={enableManualEntry}
                onPointerDown={enableManualEntry}
                value={formData.username}
                onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                placeholder="请输入用户名"
              />
            </label>

            <label className="auth-field" htmlFor="password">
              <span>密码</span>
              <input
                id="password"
                name="password"
                type="password"
                required
                autoComplete="current-password"
                readOnly={!manualEntryEnabled}
                onFocus={enableManualEntry}
                onPointerDown={enableManualEntry}
                value={formData.password}
                onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                placeholder="请输入密码"
              />
            </label>

            <button type="submit" disabled={loading} className="auth-primary">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
              {loading ? '登录中…' : '登录'}
            </button>

            <div className="auth-divider" aria-hidden="true">
              <span>或</span>
            </div>

            <button type="button" onClick={() => navigate('/register')} className="auth-secondary">
              创建新账户
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </form>

          <p className="auth-assurance">
            <BadgeCheck className="h-4 w-4" aria-hidden="true" />
            SIQ v3.1 · 智能投研分析决策系统
          </p>
        </div>
      </section>
    </main>
  )
}

export default LoginPage
