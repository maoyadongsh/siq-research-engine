import React, { useState } from 'react'
import { ArrowRight, BadgeCheck, FileSearch, Loader2, LogIn, ShieldCheck, Sparkles } from 'lucide-react'
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
    <main className="auth-shell auth-shell-login">
      <section className="auth-card auth-card-login" aria-labelledby="login-title">
        <div className="auth-copy" aria-label="SIQ Research Engine">
          <div>
            <div className="auth-copy-mark" aria-hidden="true">
              <span>SIQ</span>
            </div>
            <p className="auth-copy-kicker">Research OS</p>
            <h2>基于全链路可审计的公司、行业研究平台</h2>
            <p>
              面向财报、披露文件和审计线索，把搜索、解析、分析与复核放在同一个安静高效的界面里。
            </p>
          </div>

          <div className="auth-copy-list" aria-hidden="true">
            <div>
              <FileSearch className="h-4 w-4" />
              <span>全球市场上市公司财报检索</span>
            </div>
            <div>
              <Sparkles className="h-4 w-4" />
              <span>高精度财报解析和指标抽取</span>
            </div>
            <div>
              <ShieldCheck className="h-4 w-4" />
              <span>严格勾稽校验及全流程证据链可回溯</span>
            </div>
          </div>

          <div className="auth-copy-foot" aria-hidden="true">
            <span>Financial Research</span>
            <span>Evidence First</span>
          </div>
        </div>

        <div className="auth-form-panel">
          <div className="auth-brand auth-brand-login">
            <div className="auth-logo" aria-hidden="true">SIQ</div>
            <div>
              <p className="auth-kicker">Research Engine</p>
              <h1 id="login-title" className="auth-title">登录 SIQ</h1>
              <p className="auth-description">基于全链路可审计的公司、行业研究平台。</p>
            </div>
          </div>

          <form className="auth-form auth-login-form" onSubmit={handleSubmit} autoComplete="off" data-form-type="other">
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
                placeholder="请输入用户名"
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
            SIQ v2.0 · 企业级财务分析系统
          </p>
        </div>
      </section>
    </main>
  )
}

export default LoginPage
