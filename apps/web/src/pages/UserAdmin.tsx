import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  CheckCircle2,
  Clock3,
  Eye,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  UserCheck,
  UserCog,
  UserRound,
  UserX,
  XCircle,
} from 'lucide-react'
import { Button } from '../components/ui'
import { useToast } from '../hooks/useToast'
import { apiJson } from '../lib/apiClient'
import { useAuth } from '../hooks/useAuth'

type ApprovalStatus = 'pending' | 'approved' | 'rejected'
type RoleValue = 'viewer' | 'analyst' | 'reviewer' | 'admin' | 'super_admin'
type FilterKey = 'pending' | 'all' | 'approved' | 'rejected' | 'disabled'

type ManagedUser = {
  id: number
  username: string
  email: string
  full_name: string
  role: RoleValue
  approval_status: ApprovalStatus
  approval_note?: string | null
  is_active: boolean
  created_at?: string | null
  last_login?: string | null
}

const roleLabels: Record<RoleValue, string> = {
  viewer: '查看者',
  analyst: '分析师',
  reviewer: '复核员',
  admin: '管理员',
  super_admin: '超级管理员',
}

const statusLabels: Record<ApprovalStatus, string> = {
  pending: '待审核',
  approved: '已通过',
  rejected: '已拒绝',
}

const statusClasses: Record<ApprovalStatus, string> = {
  pending: 'secondary-status-warning',
  approved: 'secondary-status-success',
  rejected: 'secondary-status-error',
}

const filterLabels: Record<FilterKey, string> = {
  pending: '待审核',
  all: '全部',
  approved: '已通过',
  rejected: '已拒绝',
  disabled: '已停用',
}

function formatDate(value?: string | null) {
  if (!value) return '暂无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '暂无'
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function statusIcon(status: ApprovalStatus) {
  if (status === 'approved') return <CheckCircle2 className="h-4 w-4" />
  if (status === 'rejected') return <XCircle className="h-4 w-4" />
  return <Clock3 className="h-4 w-4" />
}

function StatusBadge({ user }: { user: ManagedUser }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className={`secondary-status ${statusClasses[user.approval_status]}`}>
        {statusIcon(user.approval_status)}
        {statusLabels[user.approval_status]}
      </span>
      <span className={`secondary-status ${user.is_active ? 'secondary-status-info' : ''}`}>
        {user.is_active ? '启用中' : '已停用'}
      </span>
    </div>
  )
}

function SummaryTile({ label, value, icon: Icon }: { label: string; value: number; icon: typeof Clock3 }) {
  return (
    <div className="metric-tile rounded-[20px] p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold text-text-muted">{label}</p>
        <Icon className="h-4 w-4 text-primary" />
      </div>
      <p className="mt-2 text-3xl font-bold text-text">{value}</p>
    </div>
  )
}

export default function UserAdmin() {
  const { user: currentUser } = useAuth()
  const { toast } = useToast()
  const [users, setUsers] = useState<ManagedUser[]>([])
  const [roleDrafts, setRoleDrafts] = useState<Record<number, RoleValue>>({})
  const [noteDrafts, setNoteDrafts] = useState<Record<number, string>>({})
  const [filter, setFilter] = useState<FilterKey>('pending')
  const [query, setQuery] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState('')
  const [error, setError] = useState('')

  const currentIsSuperAdmin = currentUser?.role === 'super_admin'
  const roleOptions = useMemo(() => {
    const roles: RoleValue[] = ['viewer', 'analyst', 'reviewer', 'admin']
    if (currentIsSuperAdmin) roles.push('super_admin')
    return roles
  }, [currentIsSuperAdmin])

  const loadUsers = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await apiJson<ManagedUser[]>('/api/auth/users?limit=500')
      setUsers(data)
      setRoleDrafts((current) => {
        const next: Record<number, RoleValue> = {}
        data.forEach((item) => {
          next[item.id] = (current[item.id] || item.role) as RoleValue
        })
        return next
      })
      setNoteDrafts((current) => {
        const next: Record<number, string> = {}
        data.forEach((item) => {
          next[item.id] = current[item.id] ?? item.approval_note ?? ''
        })
        return next
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : '加载用户失败'
      setError(message)
      toast({ title: '加载用户失败', description: message, type: 'error' })
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    async function init() {
      await loadUsers()
    }
    init()
  }, [loadUsers])

  const stats = useMemo(() => ({
    total: users.length,
    pending: users.filter((item) => item.approval_status === 'pending').length,
    approved: users.filter((item) => item.approval_status === 'approved').length,
    rejected: users.filter((item) => item.approval_status === 'rejected').length,
    disabled: users.filter((item) => !item.is_active).length,
  }), [users])

  const filteredUsers = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    return users.filter((item) => {
      const statusMatched =
        filter === 'all' ||
        (filter === 'disabled' ? !item.is_active : item.approval_status === filter)
      if (!statusMatched) return false
      if (!keyword) return true
      return [item.username, item.email, item.full_name, roleLabels[item.role]]
        .some((text) => (text || '').toLowerCase().includes(keyword))
    })
  }, [filter, query, users])

  const cannotManage = useCallback((target: ManagedUser) => {
    return target.role === 'super_admin' && !currentIsSuperAdmin
  }, [currentIsSuperAdmin])

  const isSelf = useCallback((target: ManagedUser) => {
    return currentUser?.id === target.id
  }, [currentUser?.id])

  const selectableUsers = useMemo(() => filteredUsers.filter((item) => !cannotManage(item) && !isSelf(item)), [filteredUsers, cannotManage, isSelf])
  const selectedUsers = useMemo(() => users.filter((item) => selectedIds.has(item.id)), [selectedIds, users])
  const allVisibleSelected = selectableUsers.length > 0 && selectableUsers.every((item) => selectedIds.has(item.id))

  function toggleUser(target: ManagedUser, checked: boolean) {
    if (cannotManage(target) || isSelf(target)) return
    setSelectedIds((current) => {
      const next = new Set(current)
      if (checked) next.add(target.id)
      else next.delete(target.id)
      return next
    })
  }

  function toggleVisible(checked: boolean) {
    setSelectedIds((current) => {
      const next = new Set(current)
      selectableUsers.forEach((item) => {
        if (checked) next.add(item.id)
        else next.delete(item.id)
      })
      return next
    })
  }

  async function updateUser(target: ManagedUser, action: string, body: object, successTitle: string) {
    const key = `${action}:${target.id}`
    setBusyKey(key)
    try {
      await apiJson(`/api/auth/users/${target.id}`, { method: 'PATCH', body })
      toast({ title: successTitle, type: 'success' })
      await loadUsers()
      setSelectedIds((current) => {
        const next = new Set(current)
        next.delete(target.id)
        return next
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : '操作失败'
      toast({ title: '操作失败', description: message, type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  async function batchUpdate(action: string, body: object, successTitle: string) {
    const user_ids = selectedUsers
      .filter((item) => !cannotManage(item) && !isSelf(item))
      .map((item) => item.id)
    if (!user_ids.length) {
      toast({ title: '请先选择可操作用户', type: 'warning' })
      return
    }

    setBusyKey(`batch:${action}`)
    try {
      const result = await apiJson<{ updated: number; skipped: number }>('/api/auth/users/batch', {
        method: 'POST',
        body: { user_ids, ...body },
      })
      toast({
        title: successTitle,
        description: `已更新 ${result.updated} 个用户${result.skipped ? `，跳过 ${result.skipped} 个` : ''}`,
        type: 'success',
      })
      setSelectedIds(new Set())
      await loadUsers()
    } catch (err) {
      const message = err instanceof Error ? err.message : '批量操作失败'
      toast({ title: '批量操作失败', description: message, type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  function isBusy(target: ManagedUser, action: string) {
    return busyKey === `${action}:${target.id}`
  }

  function isBatchBusy(action: string) {
    return busyKey === `batch:${action}`
  }

  function renderUserActions(target: ManagedUser) {
    const role = roleDrafts[target.id] || target.role
    const note = noteDrafts[target.id] || ''
    const protectedTarget = cannotManage(target)
    const selfTarget = isSelf(target)
    const roleChanged = role !== target.role

    return (
      <div className="grid gap-3">
        <label className="block space-y-2">
          <span className="text-xs font-semibold text-text-muted">角色</span>
          <select
            value={role}
            disabled={protectedTarget || selfTarget}
            onChange={(event) => setRoleDrafts((current) => ({ ...current, [target.id]: event.target.value as RoleValue }))}
            className="form-control h-10 min-h-10 w-full rounded-xl px-3 py-0 text-sm"
          >
            {roleOptions.map((item) => <option key={item} value={item}>{roleLabels[item]}</option>)}
          </select>
        </label>
        <label className="block space-y-2">
          <span className="text-xs font-semibold text-text-muted">审批备注</span>
          <textarea
            value={note}
            onChange={(event) => setNoteDrafts((current) => ({ ...current, [target.id]: event.target.value }))}
            rows={2}
            className="form-control min-h-[72px] w-full resize-y px-3 py-2 text-sm leading-6"
            placeholder="审批说明或拒绝原因"
          />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            disabled={protectedTarget || selfTarget || isBusy(target, 'approve')}
            leftIcon={isBusy(target, 'approve') ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserCheck className="h-4 w-4" />}
            onClick={() => updateUser(target, 'approve', {
              approval_status: 'approved',
              role,
              approval_note: note,
            }, '用户已通过审批')}
          >
            通过
          </Button>
          <Button
            type="button"
            size="sm"
            variant="danger"
            disabled={protectedTarget || selfTarget || isBusy(target, 'reject')}
            leftIcon={isBusy(target, 'reject') ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserX className="h-4 w-4" />}
            onClick={() => updateUser(target, 'reject', {
              approval_status: 'rejected',
              approval_note: note,
            }, '用户已拒绝')}
          >
            拒绝
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={protectedTarget || selfTarget || !roleChanged || isBusy(target, 'role')}
            leftIcon={isBusy(target, 'role') ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
            onClick={() => updateUser(target, 'role', { role }, '角色已更新')}
          >
            保存角色
          </Button>
          <Button
            type="button"
            size="sm"
            variant={target.is_active ? 'secondary' : 'primary'}
            disabled={protectedTarget || selfTarget || isBusy(target, 'active')}
            leftIcon={isBusy(target, 'active') ? <Loader2 className="h-4 w-4 animate-spin" /> : target.is_active ? <UserX className="h-4 w-4" /> : <UserCheck className="h-4 w-4" />}
            onClick={() => updateUser(target, 'active', target.is_active
              ? { is_active: false }
              : { approval_status: 'approved', is_active: true }, target.is_active ? '用户已停用' : '用户已启用')}
          >
            {target.is_active ? '停用' : '启用'}
          </Button>
        </div>
        {protectedTarget && <p className="text-xs font-medium text-text-muted">普通管理员不能管理超级管理员账户。</p>}
        {selfTarget && <p className="text-xs font-medium text-text-muted">当前登录账户不能被自己停用或撤销审批。</p>}
      </div>
    )
  }

  return (
    <div className="secondary-page min-w-0 overflow-x-hidden">
      <section className="secondary-hero">
        <div className="secondary-hero-inner">
          <div className="max-w-3xl">
            <div className="secondary-kicker">
              <ShieldCheck className="h-3.5 w-3.5" />
              User Approval
            </div>
            <h1 className="secondary-title">用户审批</h1>
            <p className="secondary-description">
              处理新注册用户、维护角色和启用状态。普通用户通过审批后进入个人工作台，数据按用户隔离。
            </p>
          </div>
          <div className="flex flex-col items-start gap-3 lg:items-end">
            <div className="secondary-step-row">
              <span className="secondary-step-chip">注册</span>
              <span className="secondary-step-chip is-active">审批</span>
              <span className="secondary-step-chip">授权</span>
            </div>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={loading}
              leftIcon={loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              onClick={loadUsers}
              className="rounded-xl"
            >
              刷新用户
            </Button>
          </div>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-5">
        <SummaryTile label="全部用户" value={stats.total} icon={UserRound} />
        <SummaryTile label="待审核" value={stats.pending} icon={Clock3} />
        <SummaryTile label="已通过" value={stats.approved} icon={UserCheck} />
        <SummaryTile label="已拒绝" value={stats.rejected} icon={UserX} />
        <SummaryTile label="已停用" value={stats.disabled} icon={XCircle} />
      </section>

      <section className="secondary-panel p-4 sm:p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div className="secondary-step-row w-full overflow-x-auto xl:w-auto">
            {(['pending', 'all', 'approved', 'rejected', 'disabled'] as FilterKey[]).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setFilter(item)}
                className={`secondary-step-chip ${filter === item ? 'is-active' : ''}`}
              >
                {filterLabels[item]}
              </button>
            ))}
          </div>
          <label className="relative block w-full xl:w-[380px]">
            <span className="sr-only">搜索用户</span>
            <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="form-control h-11 min-h-11 w-full rounded-xl py-0 pl-10 pr-3 text-sm"
              placeholder="搜索用户名、邮箱、姓名或角色"
            />
          </label>
        </div>

        <div className="secondary-panel-muted mt-4 px-4 py-3">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <label className="inline-flex min-h-10 cursor-pointer items-center gap-3 text-sm font-semibold text-text">
              <input
                type="checkbox"
                checked={allVisibleSelected}
                disabled={selectableUsers.length === 0}
                onChange={(event) => toggleVisible(event.target.checked)}
                className="h-4 w-4 accent-primary"
              />
              选择当前列表可操作用户
              <span className="text-text-muted">已选 {selectedIds.size} 个</span>
            </label>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                disabled={selectedIds.size === 0 || isBatchBusy('approve')}
                leftIcon={isBatchBusy('approve') ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserCheck className="h-4 w-4" />}
                onClick={() => batchUpdate('approve', {
                  approval_status: 'approved',
                  approval_note: '批量审批通过',
                }, '批量审批已通过')}
              >
                批量通过
              </Button>
              <Button
                type="button"
                size="sm"
                variant="danger"
                disabled={selectedIds.size === 0 || isBatchBusy('reject')}
                leftIcon={isBatchBusy('reject') ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserX className="h-4 w-4" />}
                onClick={() => batchUpdate('reject', {
                  approval_status: 'rejected',
                  approval_note: '批量审批拒绝',
                }, '批量审批已拒绝')}
              >
                批量拒绝
              </Button>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={selectedIds.size === 0 || isBatchBusy('enable')}
                leftIcon={isBatchBusy('enable') ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserCheck className="h-4 w-4" />}
                onClick={() => batchUpdate('enable', {
                  approval_status: 'approved',
                  is_active: true,
                }, '批量启用完成')}
              >
                批量启用
              </Button>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={selectedIds.size === 0 || isBatchBusy('disable')}
                leftIcon={isBatchBusy('disable') ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserX className="h-4 w-4" />}
                onClick={() => batchUpdate('disable', { is_active: false }, '批量停用完成')}
              >
                批量停用
              </Button>
            </div>
          </div>
        </div>

        {error && (
          <div className="mt-4 rounded-[18px] border border-error/20 bg-error/5 px-4 py-3 text-sm font-medium text-error">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex min-h-[300px] items-center justify-center text-text-muted">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            正在加载用户...
          </div>
        ) : filteredUsers.length === 0 ? (
          <div className="mt-5 rounded-[22px] border border-dashed border-border bg-bg px-5 py-12 text-center text-sm text-text-muted">
            当前筛选条件下没有用户。
          </div>
        ) : (
          <>
            <div className="mt-5 hidden overflow-x-auto lg:block">
              <table className="min-w-[1040px] w-full border-separate border-spacing-0 text-left">
                <thead>
                  <tr className="text-xs font-semibold uppercase tracking-wide text-text-muted">
                    <th className="w-12 border-b border-border px-4 py-3">
                      <span className="sr-only">选择</span>
                    </th>
                    <th className="border-b border-border px-4 py-3">用户</th>
                    <th className="border-b border-border px-4 py-3">状态</th>
                    <th className="border-b border-border px-4 py-3">时间</th>
                    <th className="border-b border-border px-4 py-3">审批操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredUsers.map((item) => (
                    <tr key={item.id} className="align-top">
                      <td className="border-b border-border px-4 py-4">
                        <input
                          type="checkbox"
                          checked={selectedIds.has(item.id)}
                          disabled={cannotManage(item) || isSelf(item)}
                          onChange={(event) => toggleUser(item, event.target.checked)}
                          aria-label={`选择用户 ${item.username}`}
                          className="mt-3 h-4 w-4 accent-primary"
                        />
                      </td>
                      <td className="border-b border-border px-4 py-4">
                        <div className="flex items-start gap-3">
                          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                            <UserCog className="h-5 w-5" />
                          </div>
                          <div className="min-w-0">
                            <p className="font-semibold text-text">{item.full_name || item.username}</p>
                            <p className="mt-1 text-sm text-text-muted">{item.username}</p>
                            <p className="mt-1 break-all text-sm text-text-muted">{item.email}</p>
                            <Link to={`/admin/users/${item.id}`} className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 text-xs font-semibold text-text-muted hover:bg-bg hover:text-text">
                              <Eye className="h-3.5 w-3.5" />查看详情
                            </Link>
                          </div>
                        </div>
                      </td>
                      <td className="border-b border-border px-4 py-4">
                        <StatusBadge user={item} />
                        <p className="mt-3 text-sm font-semibold text-text">{roleLabels[item.role] || item.role}</p>
                        {item.approval_note && <p className="mt-2 max-w-[220px] text-sm leading-6 text-text-muted">{item.approval_note}</p>}
                      </td>
                      <td className="border-b border-border px-4 py-4 text-sm leading-6 text-text-muted">
                        <p>注册：{formatDate(item.created_at)}</p>
                        <p>登录：{formatDate(item.last_login)}</p>
                      </td>
                      <td className="border-b border-border px-4 py-4">
                        {renderUserActions(item)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-5 grid gap-4 lg:hidden">
              {filteredUsers.map((item) => (
                <article key={item.id} className="rounded-[22px] border border-border bg-card px-4 py-4">
                  <div className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(item.id)}
                      disabled={cannotManage(item) || isSelf(item)}
                      onChange={(event) => toggleUser(item, event.target.checked)}
                      aria-label={`选择用户 ${item.username}`}
                      className="mt-3 h-4 w-4 accent-primary"
                    />
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                      <UserCog className="h-5 w-5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold text-text">{item.full_name || item.username}</p>
                      <p className="mt-1 text-sm text-text-muted">{item.username}</p>
                      <p className="mt-1 break-all text-sm text-text-muted">{item.email}</p>
                      <Link to={`/admin/users/${item.id}`} className="mt-3 inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-card px-3 text-xs font-semibold text-text-muted hover:bg-bg hover:text-text">
                        <Eye className="h-3.5 w-3.5" />查看详情
                      </Link>
                    </div>
                  </div>
                  <div className="mt-4">
                    <StatusBadge user={item} />
                    <p className="mt-3 text-sm text-text-muted">角色：<span className="font-semibold text-text">{roleLabels[item.role] || item.role}</span></p>
                    <p className="mt-1 text-sm text-text-muted">注册：{formatDate(item.created_at)}</p>
                    <p className="mt-1 text-sm text-text-muted">登录：{formatDate(item.last_login)}</p>
                  </div>
                  {item.approval_note && <p className="mt-3 rounded-xl bg-bg px-3 py-2 text-sm leading-6 text-text-muted">{item.approval_note}</p>}
                  <div className="mt-4">{renderUserActions(item)}</div>
                </article>
              ))}
            </div>
          </>
        )}
      </section>
    </div>
  )
}
