import ReportViewer from '../components/report/ReportViewer'

const FACTCHECKER_AGENT = {
  apiPrefix: '/api/factchecker',
  title: '核查助手',
  description: '校验关键数据、公式勾稽和证据来源，定位需要复核的结论。',
  quickQuestions: ['核查营收数据', '列出存疑项', '验证三大表'],
}

export default function FactVerification() {
  return <ReportViewer agentConfig={FACTCHECKER_AGENT} pageTitle="事实核查" reportType="factcheck" reportApiSuffix="factchecks" iframeTitle="事实核查报告" emptyTitle={(name) => `${name} 暂无核查报告`} emptyDescription="运行事实核查流程后，生成的 HTML 报告会在这里展示。" infoFields={(company) => [{ label: '公司', value: company.name }, { label: '代码', value: company.code }, { label: '核查', value: `${company.factcheckCount || 0} 份` }, { label: '状态', value: company.hasFactcheck ? '已生成' : '待核查' }]} />
}
