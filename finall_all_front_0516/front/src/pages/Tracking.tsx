import ReportViewer from '../components/report/ReportViewer'

const TRACKING_AGENT = {
  apiPrefix: '/api/tracking',
  title: '跟踪助手',
  description: '提取跟踪事项、舆情变化和预警信号，沉淀为持续观察报告。',
  quickQuestions: ['提取跟踪事项', '生成舆情日报', '列出预警信号'],
}

export default function Tracking() {
  return <ReportViewer agentConfig={TRACKING_AGENT} pageTitle="持续跟踪" reportType="tracking" reportApiSuffix="trackings" iframeTitle="持续跟踪报告" emptyTitle={(name) => `${name} 暂无跟踪报告`} emptyDescription="运行持续跟踪流程后，事项、舆情和预警报告会在这里展示。" infoFields={(company) => [{ label: '公司', value: company.name }, { label: '代码', value: company.code }, { label: '跟踪', value: `${company.trackingCount || 0} 份` }, { label: '状态', value: company.hasTracking ? '已生成' : '待跟踪' }]} />
}
