import ReportViewer from '../components/report/ReportViewer'

const ANALYSIS_AGENT = {
  apiPrefix: '/api/analysis',
  title: '分析助手',
  description: '围绕当前财报生成研究结论、偿债能力评估与同业对比。',
  quickQuestions: [
    '生成深度分析',
    '评估偿债能力',
    '对比同业表现',
  ],
}

export default function AnalysisReport() {
  return <ReportViewer agentConfig={ANALYSIS_AGENT} pageTitle="智能分析" reportType="analysis" reportApiSuffix="reports" iframeTitle="智能分析" emptyTitle={(name) => `${name} 暂无分析`} emptyDescription="先完成财报解析，生成后的分析报告会在这里展示。" infoFields={(company) => [{ label: '公司', value: company.name }, { label: '代码', value: company.code }, { label: '报告', value: `${company.reportCount} 份` }, { label: '状态', value: company.hasReport ? '已生成' : '待生成' }]} />
}
