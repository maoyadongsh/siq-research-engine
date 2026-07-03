# TOOLS.md - SIQ 投委会法务专家

## 可用工具

### 信息采集
- `web_search` / `tavily_search` / `web_search_exa` — 法律法规、司法案例、合规动态
- `web_fetch` / `tavily_extract` / `crawling_exa` — 抓取裁判文书、行政处罚
- `tavily_research` — 综合性研究（适合法律深度分析）

### 企业信息
- `advSearch` / `getBasicInfo` / `getEnterpriseInfo` — 企业工商信息
- `getAllRiskInfo` / `getEnterpriseCountInfo` — 综合风险（含司法维度）
- `sumLawsuit` — 整体诉讼统计
- `getExecutionListByName` / `getExecutedpersonListByName` — 失信与被执行
- `getEmployees` — 主要人员
- `getChangeRecords` — 工商变更记录

### 协作
- `sessions_send` — 向 coordinator 回复报告
- `read` / `write` / `edit` — 读写项目共享文件

## 报告输出路径
```
/home/maoyd/siq-research-engine/data/wiki/deals/{deal_id}/
```
