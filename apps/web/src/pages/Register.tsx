import React, { useState } from 'react'
import { ArrowLeft, CheckCircle2, Loader2, UserPlus } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { apiJson } from '@/shared/api/client'
import type { User } from '../hooks/useAuth'

interface RegisterFormData {
  username: string
  email: string
  password: string
  password2: string
  full_name: string
}

interface RegisterResponse {
  message: string
  status: 'pending' | string
  user: User
}

export function RegisterPage() {
  const [formData, setFormData] = useState<RegisterFormData>({
    username: '',
    email: '',
    password: '',
    password2: '',
    full_name: '',
  })
  const [error, setError] = useState<string>('')
  const [submitted, setSubmitted] = useState(false)
  const [submittedMessage, setSubmittedMessage] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const validateForm = (): string | null => {
    if (!formData.username || formData.username.length < 3) return '用户名至少需要 3 个字符'
    if (!formData.email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) return '请输入有效的邮箱地址'
    if (!formData.password || formData.password.length < 8) return '密码至少需要 8 个字符'
    if (formData.password !== formData.password2) return '两次输入的密码不一致'
    return null
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    const validationError = validateForm()
    if (validationError) {
      setError(validationError)
      return
    }

    setLoading(true)

    try {
      const data = await apiJson<RegisterResponse>('/api/auth/register', {
        method: 'POST',
        body: {
          username: formData.username,
          email: formData.email,
          password: formData.password,
          full_name: formData.full_name || formData.username,
        },
      })

      setSubmitted(true)
      setSubmittedMessage(data.message || '注册申请已提交，请等待管理员审核')
    } catch (err) {
      setError(err instanceof Error ? err.message : '注册失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card auth-card-register" aria-labelledby="register-title">
        <div className="auth-register-head">
          <div className="auth-form-kicker auth-register-kicker" aria-hidden="true">SIQ Research Engine</div>
          <h1 id="register-title" className="auth-title">创建账户</h1>
          <p className="auth-description">提交后由管理员审核开通权限</p>
        </div>

        {submitted ? (
          <div className="auth-form">
            <div className="auth-success" role="status">
              <CheckCircle2 className="h-5 w-5" aria-hidden="true" />
              <span>{submittedMessage}</span>
            </div>
            <button type="button" onClick={() => navigate('/login')} className="auth-primary">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              返回登录
            </button>
          </div>
        ) : (
          <form className="auth-form" onSubmit={handleSubmit}>
            {error && (
              <div className="auth-alert" role="alert">
                {error}
              </div>
            )}

            <label className="auth-field" htmlFor="username">
              <span>用户名 <b className="auth-required" aria-hidden="true">*</b></span>
              <input
                id="username"
                name="username"
                type="text"
                required
                minLength={3}
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                value={formData.username}
                onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                placeholder="至少 3 个字符"
              />
            </label>

            <label className="auth-field" htmlFor="email">
              <span>邮箱 <b className="auth-required" aria-hidden="true">*</b></span>
              <input
                id="email"
                name="email"
                type="email"
                required
                autoComplete="email"
                spellCheck={false}
                value={formData.email}
                onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                placeholder="请输入邮箱地址"
              />
            </label>

            <label className="auth-field" htmlFor="full_name">
              <span>姓名</span>
              <input
                id="full_name"
                name="full_name"
                type="text"
                autoComplete="name"
                value={formData.full_name}
                onChange={(e) => setFormData({ ...formData, full_name: e.target.value })}
                placeholder="请输入真实姓名（可选）"
              />
            </label>

            <label className="auth-field" htmlFor="password">
              <span>密码 <b className="auth-required" aria-hidden="true">*</b></span>
              <input
                id="password"
                name="password"
                type="password"
                required
                minLength={8}
                autoComplete="new-password"
                value={formData.password}
                onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                placeholder="至少 8 个字符"
              />
            </label>

            <label className="auth-field" htmlFor="password2">
              <span>确认密码 <b className="auth-required" aria-hidden="true">*</b></span>
              <input
                id="password2"
                name="password2"
                type="password"
                required
                autoComplete="new-password"
                value={formData.password2}
                onChange={(e) => setFormData({ ...formData, password2: e.target.value })}
                placeholder="再次输入密码"
              />
            </label>

            <button type="submit" disabled={loading} className="auth-primary">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserPlus className="h-4 w-4" />}
              {loading ? '注册中…' : '注册'}
            </button>

            <div className="auth-register-divider" aria-hidden="true">
              <span>已有账户？</span>
            </div>

            <button type="button" onClick={() => navigate('/login')} className="auth-secondary">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              返回登录
            </button>
          </form>
        )}

        <p className="auth-version">SIQ v3.1 · 智能投研分析决策系统</p>
      </section>
    </main>
  )
}
