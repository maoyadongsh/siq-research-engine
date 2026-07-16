import ReportViewer from '../components/report/ReportViewer'
import { trackingQuickQuestions } from '../lib/quickQuestions'

const TRACKING_AGENT = {
  apiPrefix: '/api/tracking',
  title: '跟踪助手',
  description: '提取跟踪事项、舆情变化和预警信号，沉淀为持续观察报告。',
  quickQuestions: trackingQuickQuestions,
}

export default function Tracking() {
  return <ReportViewer agentConfig={TRACKING_AGENT} pageTitle="持续跟踪" reportType="tracking" reportApiSuffix="trackings" iframeTitle="持续跟踪报告" emptyTitle={(name) => `${name} 暂无跟踪报告`} emptyDescription="运行持续跟踪流程后，事项、舆情和预警报告会在这里展示。" infoFields={(company) => [{ label: '公司', value: company.name }, { label: '代码', value: company.code }, { label: '跟踪', value: `${company.trackingCount || 0} 份` }, { label: '状态', value: company.hasTracking ? '已生成' : '待跟踪' }]} marketScope="all-parsed" />
}
