import { Link } from 'react-router-dom'
import { ArrowLeft, ShieldAlert } from 'lucide-react'
import { EmptyState, PageShell } from '@/components/page'

export default function Forbidden() {
  return (
    <PageShell variant="secondary">
      <div className="flex min-h-[calc(100vh-var(--app-topbar-height)-2rem)] items-center justify-center px-4 py-10">
        <EmptyState
          icon={ShieldAlert}
          title="无权访问当前页面"
          description="当前账号没有查看这部分内容的权限。"
          action={
            <Link
              to="/"
              className="inline-flex h-10 items-center gap-2 rounded-xl accent-gradient px-4 text-sm font-semibold text-white shadow-lg shadow-blue-900/15 transition hover:-translate-y-0.5 hover:brightness-110"
            >
              <ArrowLeft className="h-4 w-4" />
              返回工作平台
            </Link>
          }
        />
      </div>
    </PageShell>
  )
}
