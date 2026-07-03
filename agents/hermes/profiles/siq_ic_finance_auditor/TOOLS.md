# TOOLS.md - SIQ 投委会财务专家

## 可用工具

### 信息采集
- `web_search` / `tavily_search` / `web_search_exa` — 财务数据、估值对标、行业财务指标
- `web_fetch` / `tavily_extract` / `crawling_exa` — 抓取财报、招股书、券商研报
- `tavily_research` — 综合性研究（适合财务深度分析）

### 企业信息
- `advSearch` / `getBasicInfo` / `getEnterpriseInfo` / `getAllRiskInfo` — 企业工商与风险
- `getExecutedpersonListByName` / `getExecutionListByName` — 被执行信息
- `getEnterpriseCountInfo` — 企业风险扫描（含财务维度）

### 协作
- `sessions_send` — 向 coordinator 回复报告
- `read` / `write` / `edit` — 读写项目共享文件

## 报告输出路径
```
/home/maoyd/siq-research-engine/data/wiki/deals/{deal_id}/
```
