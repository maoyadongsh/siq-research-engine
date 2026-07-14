import { Link } from 'react-router-dom'
import { MicOff } from 'lucide-react'

import { EmptyState, PageSection, PageShell } from '@/components/page'
import { Button } from '@/components/ui/button'

export default function MeetingUnavailable() {
  return (
    <PageShell variant="secondary">
      <PageSection>
        <EmptyState
          icon={MicOff}
          title="会议转写暂未开放"
          description="当前环境未启用会议转写，其他研究和问答功能不受影响。"
          action={<Button asChild variant="secondary"><Link to="/">返回工作平台</Link></Button>}
        />
      </PageSection>
    </PageShell>
  )
}
