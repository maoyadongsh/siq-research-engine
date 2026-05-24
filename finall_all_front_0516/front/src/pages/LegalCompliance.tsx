import ReportViewer from '../components/report/ReportViewer'

const LEGAL_AGENT = {
  apiPrefix: '/api/legal',
  title: '法务助手',
  description: '检索法规依据、梳理合规风险，并生成 HTML 法律意见书。',
  quickQuestions: ['生成法律意见书', '检索法规依据', '列出合规风险'],
}

export default function LegalCompliance() {
  return <ReportViewer agentConfig={LEGAL_AGENT} pageTitle="法务合规" reportType="legal" reportApiSuffix="legals" iframeTitle="法律意见书" emptyTitle={(name) => `${name} 暂无法律意见书`} emptyDescription="运行法务合规流程后，智能体出具的 HTML 法律意见书会在这里展示。" infoFields={(company) => [{ label: '公司', value: company.name }, { label: '代码', value: company.code }, { label: '意见书', value: `${company.legalCount || 0} 份` }, { label: '状态', value: company.hasLegal ? '已生成' : '待出具' }]} />
}
